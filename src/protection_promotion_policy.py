"""Versioned protection-promotion rubric (M13 Issue 3). ADVISORY ONLY.

Consumes the cross-run ledger (Issue 2) and decides whether a batch of
protection observations is *eligible for independent human review* —
nothing more. This module cannot promote anything: see "What this module
must never do" below, enforced structurally, not by convention.

## The five states this module must never confuse

1. A broker-authoritative observation exists — a fact about one
   `LedgerObservationSummary` (`evidence_source_class` in
   `BROKER_AUTHORITATIVE_SOURCE_CLASSES`), decided in Issue 1.
2. Evidence qualifies under this rubric — what `evaluate_promotion_
   eligibility` below computes.
3. Evidence is eligible for human review — `PromotionOutcome.
   ELIGIBLE_FOR_REVIEW` / `REVIEW_REQUIRED`, still just this module's
   opinion.
4. Human approval occurred — belongs to Issue 6 of the milestone handoff
   (`PROTECTION_PROMOTION_REVIEW.md`), a document a human fills in. No
   function in this module produces that record.
5. Runtime authority changed — `ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES`
   in `src/live_order_state_model.py`. Nothing in this module, or in this
   whole milestone's Issues 1-5, may write to it.

## What this module must never do

- Set `APPROVED_FOR_ACTIVE_PROTECTION`.
- Modify `ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES` or
  `ACTIVATION_APPROVALS`.
- Set `protection_grounded`, `activation_approved`,
  `permits_automated_action`, `live_ready`, or `unattended_ready`.
- Connect to a broker, execute a checkpoint, or mutate broker state.
- Automatically promote anything. `ProtectionPromotionPolicy.
  automatic_promotion_permitted` cannot be constructed as `True` — there is
  no policy configuration, present or future, under which this module's
  output can trigger an automated promotion.

`PromotionAssessment.promotion_permitted` is likewise hard-coded `False` on
every instance, regardless of policy. `promotion_enabled` and
`independent_review_required` are ordinary, safely-defaulted policy
booleans — they only steer which advisory outcome label is reported for
otherwise-identical evidence (`PROMOTION_NOT_PERMITTED` vs.
`REVIEW_REQUIRED` vs. `ELIGIBLE_FOR_REVIEW`); none of the three ever causes
an authority change, because this module has no path to one.

## Q2 is not answered here

`minimum_distinct_eligible_live_runs` and `minimum_distinct_observation_
times` on `DEFAULT_PROTECTION_PROMOTION_POLICY` are conservative
placeholders (`threshold_pending_independent_review=True`), not a
product-owner decision. They exist so Milestone 13's Issues 1-3 can be
built and tested now, per the roadmap's own principle: "a milestone that
only produces documentation is legitimate when the thing being decided is
a policy, not a mechanism — write the rubric before collecting the data it
judges." Changing them requires an explicit, reviewed policy version bump,
never a silent code edit.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from src.live_order_state_model import ProtectiveCoverage
from src.protection_observation_ledger import LedgerObservationSummary
from src.protection_observation_model import (
    BROKER_AUTHORITATIVE_SOURCE_CLASSES,
    ObservationConsistencyStatus,
    ObservationContaminationStatus,
    PrivacyRedactionStatus,
    QuantityRelationship,
)
from src.read_only_broker_state import SnapshotFreshness


class ProtectionPromotionPolicyError(ValueError):
    """A malformed policy or evaluation input. Never a broker condition."""


class PromotionOutcome(str, Enum):
    """Every outcome `evaluate_promotion_eligibility` may return.

    Deterministic and exhaustive: exactly one member is returned per
    evaluation, chosen by the fixed priority order documented on
    `evaluate_promotion_eligibility`.
    """

    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    ELIGIBLE_FOR_REVIEW = "ELIGIBLE_FOR_REVIEW"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
    STALE_EVIDENCE = "STALE_EVIDENCE"
    CONTAMINATED_EVIDENCE = "CONTAMINATED_EVIDENCE"
    ACCOUNT_MISMATCH = "ACCOUNT_MISMATCH"
    RELEASE_MISMATCH = "RELEASE_MISMATCH"
    IMPLEMENTATION_MISMATCH = "IMPLEMENTATION_MISMATCH"
    SOURCE_INELIGIBLE = "SOURCE_INELIGIBLE"
    COVERAGE_INCOMPLETE = "COVERAGE_INCOMPLETE"
    PROTECTION_NOT_ACTIVE = "PROTECTION_NOT_ACTIVE"
    PROTECTION_UNVERIFIABLE = "PROTECTION_UNVERIFIABLE"
    PRIVACY_UNVERIFIED = "PRIVACY_UNVERIFIED"
    NONZERO_WRITE_EVIDENCE = "NONZERO_WRITE_EVIDENCE"
    PROMOTION_NOT_PERMITTED = "PROMOTION_NOT_PERMITTED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


#: Outcomes that represent a legitimate, non-failure result. In particular,
#: `PROTECTION_NOT_ACTIVE` (the account was flat every time it was observed)
#: is not an implementation failure — per the milestone decision on Q1,
#: absence of protection must complete the observation normally.
NON_FAILURE_OUTCOMES: frozenset[PromotionOutcome] = frozenset(
    {
        PromotionOutcome.PROTECTION_NOT_ACTIVE,
        PromotionOutcome.ELIGIBLE_FOR_REVIEW,
        PromotionOutcome.REVIEW_REQUIRED,
        PromotionOutcome.PROMOTION_NOT_PERMITTED,
    }
)


@dataclass(frozen=True)
class ProtectionPromotionPolicy:
    """A versioned, immutable evidence-sufficiency rubric.

    `automatic_promotion_permitted` is validated to always be `False` —
    there is no way to construct this type with it `True`. Every other
    field is an ordinary configuration value with a safe default.
    """

    policy_version: str
    promotion_enabled: bool
    automatic_promotion_permitted: bool
    independent_review_required: bool
    minimum_distinct_eligible_live_runs: int
    minimum_distinct_observation_times: int
    maximum_evidence_age_seconds: float
    require_release_consistency: bool
    require_implementation_digest_consistency: bool
    require_account_fingerprint_consistency: bool
    require_broker_authoritative_source: bool
    require_active_protection_state: bool
    require_exact_quantity_coverage: bool
    reject_contradictory_evidence: bool
    reject_contaminated_evidence: bool
    reject_stale_evidence: bool
    require_zero_write_proof: bool
    require_privacy_verification: bool
    require_operator_presence_attestation: bool
    threshold_pending_independent_review: bool

    def __post_init__(self) -> None:
        if not isinstance(self.policy_version, str) or not self.policy_version.strip():
            raise ProtectionPromotionPolicyError(
                "policy_version must be a non-blank string"
            )
        for name in (
            "promotion_enabled",
            "automatic_promotion_permitted",
            "independent_review_required",
            "require_release_consistency",
            "require_implementation_digest_consistency",
            "require_account_fingerprint_consistency",
            "require_broker_authoritative_source",
            "require_active_protection_state",
            "require_exact_quantity_coverage",
            "reject_contradictory_evidence",
            "reject_contaminated_evidence",
            "reject_stale_evidence",
            "require_zero_write_proof",
            "require_privacy_verification",
            "require_operator_presence_attestation",
            "threshold_pending_independent_review",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ProtectionPromotionPolicyError(f"{name} must be a bool")

        if self.automatic_promotion_permitted:
            # Structural, not conventional. There is no configuration, now
            # or in any future policy version, under which this rubric may
            # trigger an automated promotion. See Issue 3's exclusions.
            raise ProtectionPromotionPolicyError(
                "automatic promotion may never be permitted by this policy"
            )

        for name in (
            "minimum_distinct_eligible_live_runs",
            "minimum_distinct_observation_times",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ProtectionPromotionPolicyError(
                    f"{name} must be a positive int"
                )

        if (
            not isinstance(self.maximum_evidence_age_seconds, (int, float))
            or isinstance(self.maximum_evidence_age_seconds, bool)
            or self.maximum_evidence_age_seconds <= 0
        ):
            raise ProtectionPromotionPolicyError(
                "maximum_evidence_age_seconds must be a positive number"
            )


#: THE DEFAULT POLICY. Safe. Conservative. Not a product-owner approval.
#:
#: `minimum_distinct_eligible_live_runs` and `minimum_distinct_observation_
#: times` are placeholders pending the independent review that Q2 requires
#: (`threshold_pending_independent_review=True`). Every rejection-side
#: default denies by default, matching `risk_limits`' posture: unknown,
#: stale, contaminated, mismatched, or unverified evidence never qualifies.
DEFAULT_PROTECTION_PROMOTION_POLICY = ProtectionPromotionPolicy(
    policy_version="m13-promotion-policy-v1-pending-independent-review",
    promotion_enabled=False,
    automatic_promotion_permitted=False,
    independent_review_required=True,
    minimum_distinct_eligible_live_runs=3,
    minimum_distinct_observation_times=3,
    maximum_evidence_age_seconds=float(7 * 24 * 60 * 60),
    require_release_consistency=True,
    require_implementation_digest_consistency=True,
    require_account_fingerprint_consistency=True,
    require_broker_authoritative_source=True,
    require_active_protection_state=True,
    require_exact_quantity_coverage=True,
    reject_contradictory_evidence=True,
    reject_contaminated_evidence=True,
    reject_stale_evidence=True,
    require_zero_write_proof=True,
    require_privacy_verification=True,
    require_operator_presence_attestation=True,
    threshold_pending_independent_review=True,
)


@dataclass(frozen=True)
class PromotionAssessment:
    """The rubric's opinion about one batch of evidence. Advisory only.

    `promotion_permitted` is hard-coded `False` on every instance,
    regardless of `policy.promotion_enabled` — this is the actual "never
    grants authority" guarantee, kept structurally independent of the
    advisory `outcome` label. `automatic_promotion_permitted` mirrors the
    policy's own frozen invariant for convenience and is likewise always
    `False`.
    """

    outcome: PromotionOutcome
    policy_version: str
    evaluated_at_monotonic: float
    observation_count: int
    protective_observation_count: int
    distinct_run_count: int
    distinct_observation_time_count: int
    independent_review_required: bool
    promotion_permitted: bool = False
    automatic_promotion_permitted: bool = False

    def __post_init__(self) -> None:
        if self.promotion_permitted:
            raise ProtectionPromotionPolicyError(
                "a promotion assessment may never assert promotion_permitted"
            )
        if self.automatic_promotion_permitted:
            raise ProtectionPromotionPolicyError(
                "a promotion assessment may never assert "
                "automatic_promotion_permitted"
            )

    @property
    def is_non_failure(self) -> bool:
        return self.outcome in NON_FAILURE_OUTCOMES


def _is_stale(
    observation: LedgerObservationSummary,
    *,
    evaluated_at_monotonic: float,
    policy: ProtectionPromotionPolicy,
) -> bool:
    if observation.data_freshness is not SnapshotFreshness.FRESH:
        return True
    age = evaluated_at_monotonic - observation.observed_at_monotonic
    return age > policy.maximum_evidence_age_seconds


def evaluate_promotion_eligibility(
    observations: Sequence[LedgerObservationSummary],
    *,
    policy: ProtectionPromotionPolicy = DEFAULT_PROTECTION_PROMOTION_POLICY,
    evaluated_at_monotonic: float,
    expected_release_identity: str,
    expected_implementation_digest: str,
    expected_account_fingerprint_reference: str,
    conflict_detected: bool = False,
    operator_presence_attested: bool = False,
) -> PromotionAssessment:
    """Evaluate one batch of evidence. Deterministic. Never mutates the
    ledger, never mutates any authority module, never performs I/O.

    `observations` is typically the result of a ledger query — e.g.
    `ledger.observations_for_run(run_id)` or a caller-filtered subset of
    `ledger.all_observations()` for one position — evaluated via the
    ledger's public query surface (Issue 2), not by re-deriving ledger
    state independently.

    Priority order (first match determines `outcome`):

    1. `conflict_detected` (surfaced from `ledger.conflicting_
       observations()`) -> `CONFLICTING_EVIDENCE`.
    2. No observations -> `INSUFFICIENT_EVIDENCE`.
    3. Any nonzero `write_count` -> `NONZERO_WRITE_EVIDENCE`.
    4. Any privacy status other than verified -> `PRIVACY_UNVERIFIED`.
    5. Any contaminated observation -> `CONTAMINATED_EVIDENCE`.
    6. Any account-fingerprint mismatch -> `ACCOUNT_MISMATCH`.
    7. Any release-identity mismatch -> `RELEASE_MISMATCH`.
    8. Any implementation-digest mismatch -> `IMPLEMENTATION_MISMATCH`.
    9. Any non-broker-authoritative source -> `SOURCE_INELIGIBLE`.
    10. Any stale observation -> `STALE_EVIDENCE`.
    11. Both protective and flat observations present -> `CONFLICTING_
        EVIDENCE`.
    12. No protective observations at all -> `PROTECTION_NOT_ACTIVE`
        (legitimate; not a failure — see Q1's decision).
    13. Any protective observation with incomplete quantity coverage ->
        `COVERAGE_INCOMPLETE`.
    14. Any protective observation internally inconsistent (its own F2
        confirmation read disagreed) -> `PROTECTION_UNVERIFIABLE`.
    15. Missing operator-presence attestation, when required, or too few
        distinct qualifying runs/times -> `INSUFFICIENT_EVIDENCE`.
    16. Otherwise: `PROMOTION_NOT_PERMITTED` if `promotion_enabled` is
        `False`; else `REVIEW_REQUIRED` if `independent_review_required`
        is `True`; else `ELIGIBLE_FOR_REVIEW`.

    Every branch above is a batch-wide ("any"/"all") check rather than a
    per-observation filter: one contaminating or mismatched record blocks
    the whole batch rather than being silently dropped. Silent filtering
    would hide exactly the kind of problem this rubric exists to surface.
    """
    if not isinstance(policy, ProtectionPromotionPolicy):
        raise ProtectionPromotionPolicyError(
            "policy must be a ProtectionPromotionPolicy"
        )
    if (
        not isinstance(evaluated_at_monotonic, (int, float))
        or isinstance(evaluated_at_monotonic, bool)
    ):
        raise ProtectionPromotionPolicyError(
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
            raise ProtectionPromotionPolicyError(f"{name} must be a non-blank string")

    def result(outcome: PromotionOutcome) -> PromotionAssessment:
        protective = [
            o
            for o in observations
            if o.normalized_protection_state is not ProtectiveCoverage.NOT_PROTECTED
        ]
        return PromotionAssessment(
            outcome=outcome,
            policy_version=policy.policy_version,
            evaluated_at_monotonic=float(evaluated_at_monotonic),
            observation_count=len(observations),
            protective_observation_count=len(protective),
            distinct_run_count=len({o.run_id for o in protective}),
            distinct_observation_time_count=len(
                {o.observed_at_monotonic for o in protective}
            ),
            independent_review_required=policy.independent_review_required,
        )

    if conflict_detected and policy.reject_contradictory_evidence:
        return result(PromotionOutcome.CONFLICTING_EVIDENCE)

    if not observations:
        return result(PromotionOutcome.INSUFFICIENT_EVIDENCE)

    if policy.require_zero_write_proof and any(
        o.write_count != 0 for o in observations
    ):
        return result(PromotionOutcome.NONZERO_WRITE_EVIDENCE)

    if policy.require_privacy_verification and any(
        o.privacy_redaction_status is not PrivacyRedactionStatus.VERIFIED_REDACTED
        for o in observations
    ):
        return result(PromotionOutcome.PRIVACY_UNVERIFIED)

    if policy.reject_contaminated_evidence and any(
        o.contamination_status is ObservationContaminationStatus.CONTAMINATED
        for o in observations
    ):
        return result(PromotionOutcome.CONTAMINATED_EVIDENCE)

    if policy.require_account_fingerprint_consistency and any(
        o.account_fingerprint_reference != expected_account_fingerprint_reference
        for o in observations
    ):
        return result(PromotionOutcome.ACCOUNT_MISMATCH)

    if policy.require_release_consistency and any(
        o.release_identity != expected_release_identity for o in observations
    ):
        return result(PromotionOutcome.RELEASE_MISMATCH)

    if policy.require_implementation_digest_consistency and any(
        o.implementation_digest != expected_implementation_digest
        for o in observations
    ):
        return result(PromotionOutcome.IMPLEMENTATION_MISMATCH)

    if policy.require_broker_authoritative_source and any(
        o.evidence_source_class not in BROKER_AUTHORITATIVE_SOURCE_CLASSES
        for o in observations
    ):
        return result(PromotionOutcome.SOURCE_INELIGIBLE)

    if policy.reject_stale_evidence and any(
        _is_stale(
            o, evaluated_at_monotonic=evaluated_at_monotonic, policy=policy
        )
        for o in observations
    ):
        return result(PromotionOutcome.STALE_EVIDENCE)

    protective_observations = [
        o
        for o in observations
        if o.normalized_protection_state is not ProtectiveCoverage.NOT_PROTECTED
    ]
    flat_observations = [
        o
        for o in observations
        if o.normalized_protection_state is ProtectiveCoverage.NOT_PROTECTED
    ]

    if (
        policy.reject_contradictory_evidence
        and protective_observations
        and flat_observations
    ):
        return result(PromotionOutcome.CONFLICTING_EVIDENCE)

    if policy.require_active_protection_state and not protective_observations:
        return result(PromotionOutcome.PROTECTION_NOT_ACTIVE)

    if policy.require_exact_quantity_coverage and any(
        o.quantity_relationship is not QuantityRelationship.EXACT_COVERAGE
        for o in protective_observations
    ):
        return result(PromotionOutcome.COVERAGE_INCOMPLETE)

    if any(
        o.consistency_status
        is ObservationConsistencyStatus.INCONSISTENT_WITHIN_OBSERVATION
        for o in protective_observations
    ):
        return result(PromotionOutcome.PROTECTION_UNVERIFIABLE)

    if policy.require_operator_presence_attestation and not operator_presence_attested:
        return result(PromotionOutcome.INSUFFICIENT_EVIDENCE)

    distinct_runs = {o.run_id for o in protective_observations}
    distinct_times = {o.observed_at_monotonic for o in protective_observations}
    if (
        len(distinct_runs) < policy.minimum_distinct_eligible_live_runs
        or len(distinct_times) < policy.minimum_distinct_observation_times
    ):
        return result(PromotionOutcome.INSUFFICIENT_EVIDENCE)

    if not policy.promotion_enabled:
        return result(PromotionOutcome.PROMOTION_NOT_PERMITTED)
    if policy.independent_review_required:
        return result(PromotionOutcome.REVIEW_REQUIRED)
    return result(PromotionOutcome.ELIGIBLE_FOR_REVIEW)


__all__ = [
    "ProtectionPromotionPolicyError",
    "PromotionOutcome",
    "NON_FAILURE_OUTCOMES",
    "ProtectionPromotionPolicy",
    "DEFAULT_PROTECTION_PROMOTION_POLICY",
    "PromotionAssessment",
    "evaluate_promotion_eligibility",
]
