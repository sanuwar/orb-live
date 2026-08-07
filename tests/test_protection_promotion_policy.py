"""Versioned protection-promotion rubric (M13 Issue 3).

Every test uses `tmp_path` for the ledger. Nothing here touches a broker,
the network, or a real clock. The rubric never mutates the ledger or any
authority module -- verified directly below.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from src.execution_gates import ACTIVATION_APPROVALS
from src.live_order_state_model import ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES
from src.protection_observation_ledger import ProtectionObservationLedger
from src.protection_promotion_policy import (
    DEFAULT_PROTECTION_PROMOTION_POLICY,
    NON_FAILURE_OUTCOMES,
    PromotionOutcome,
    ProtectionPromotionPolicy,
    ProtectionPromotionPolicyError,
    evaluate_promotion_eligibility,
)
from tests._protection_observation_fixtures import (
    RELEASE_SHA,
    active_protective_order,
    digest,
    imported_evidence,
    mismatched_account_fingerprint,
    mismatched_implementation_digest,
    no_protective_order,
    over_coverage,
    partial_quantity_coverage,
    simulated_evidence,
    stale_protective_order,
    terminal_protective_order,
)

EXPECTED_ACCOUNT = digest("account")
EXPECTED_IMPLEMENTATION = digest("implementation")


def _ledger(tmp_path: Path, run_id: str = "run-0001") -> ProtectionObservationLedger:
    return ProtectionObservationLedger(tmp_path / "ledger.sqlite3", run_id)


def _evaluate(observations, **overrides):
    kwargs = dict(
        evaluated_at_monotonic=1000.0,
        expected_release_identity=RELEASE_SHA,
        expected_implementation_digest=EXPECTED_IMPLEMENTATION,
        expected_account_fingerprint_reference=EXPECTED_ACCOUNT,
        operator_presence_attested=True,
    )
    kwargs.update(overrides)
    return evaluate_promotion_eligibility(observations, **kwargs)


def _permissive_policy(**overrides) -> ProtectionPromotionPolicy:
    base = dict(
        policy_version="test-policy-v1",
        promotion_enabled=True,
        automatic_promotion_permitted=False,
        independent_review_required=False,
        minimum_distinct_eligible_live_runs=2,
        minimum_distinct_observation_times=2,
        maximum_evidence_age_seconds=1_000_000.0,
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
        require_operator_presence_attestation=False,
        threshold_pending_independent_review=True,
    )
    base.update(overrides)
    return ProtectionPromotionPolicy(**base)


# --------------------------------------------------------------------------
# Policy construction invariants.
# --------------------------------------------------------------------------


def test_automatic_promotion_permitted_can_never_be_constructed_true():
    with pytest.raises(ProtectionPromotionPolicyError):
        _permissive_policy(automatic_promotion_permitted=True)


def test_default_policy_is_safe():
    policy = DEFAULT_PROTECTION_PROMOTION_POLICY
    assert policy.promotion_enabled is False
    assert policy.automatic_promotion_permitted is False
    assert policy.independent_review_required is True
    assert policy.threshold_pending_independent_review is True


def test_policy_rejects_non_positive_thresholds():
    with pytest.raises(ProtectionPromotionPolicyError):
        _permissive_policy(minimum_distinct_eligible_live_runs=0)
    with pytest.raises(ProtectionPromotionPolicyError):
        _permissive_policy(maximum_evidence_age_seconds=0.0)


# --------------------------------------------------------------------------
# Evidentiary scenarios.
# --------------------------------------------------------------------------


def test_zero_observations_is_insufficient(tmp_path):
    assessment = _evaluate([])
    assert assessment.outcome is PromotionOutcome.INSUFFICIENT_EVIDENCE


def test_only_no_protective_order_records_is_protection_not_active(tmp_path):
    with _ledger(tmp_path) as ledger:
        ledger.append(no_protective_order("run-0001", observation_id="a"))
        ledger.append(no_protective_order("run-0001", observation_id="b"))
        observations = ledger.all_observations()
    assessment = _evaluate(observations)
    assert assessment.outcome is PromotionOutcome.PROTECTION_NOT_ACTIVE
    assert assessment.outcome in NON_FAILURE_OUTCOMES


def test_one_eligible_active_live_observation_is_insufficient_run_count(tmp_path):
    with _ledger(tmp_path) as ledger:
        ledger.append(active_protective_order("run-0001", observation_id="a"))
        observations = ledger.all_observations()
    # One run, one time: below the default policy's minimum of 3.
    assessment = _evaluate(observations)
    assert assessment.outcome is PromotionOutcome.INSUFFICIENT_EVIDENCE


def test_multiple_distinct_eligible_live_observations_reach_promotion_not_permitted(
    tmp_path,
):
    path = tmp_path / "ledger.sqlite3"
    observations = []
    for index, run_id in enumerate(("run-a", "run-b", "run-c")):
        with ProtectionObservationLedger(path, run_id) as ledger:
            ledger.append(
                active_protective_order(
                    run_id, observation_id=f"obs-{index}",
                    observed_at_monotonic=100.0 + index,
                )
            )
    with ProtectionObservationLedger(path, "run-c") as ledger:
        observations = ledger.all_observations()
    assessment = _evaluate(observations)
    # Default policy: evidence is otherwise sufficient, but promotion is
    # disabled by default -- PROMOTION_NOT_PERMITTED, never eligibility.
    assert assessment.outcome is PromotionOutcome.PROMOTION_NOT_PERMITTED
    assert assessment.promotion_permitted is False


def test_permissive_policy_reaches_review_required_then_eligible(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    for index, run_id in enumerate(("run-a", "run-b")):
        with ProtectionObservationLedger(path, run_id) as ledger:
            ledger.append(
                active_protective_order(
                    run_id, observation_id=f"obs-{index}",
                    observed_at_monotonic=100.0 + index,
                )
            )
    with ProtectionObservationLedger(path, "run-b") as ledger:
        observations = ledger.all_observations()

    review_required_policy = _permissive_policy(independent_review_required=True)
    assessment = _evaluate(observations, policy=review_required_policy)
    assert assessment.outcome is PromotionOutcome.REVIEW_REQUIRED
    assert assessment.promotion_permitted is False

    eligible_policy = _permissive_policy(independent_review_required=False)
    assessment = _evaluate(observations, policy=eligible_policy)
    assert assessment.outcome is PromotionOutcome.ELIGIBLE_FOR_REVIEW
    assert assessment.promotion_permitted is False


def test_simulated_only_evidence_is_source_ineligible(tmp_path):
    with _ledger(tmp_path) as ledger:
        ledger.append(simulated_evidence("run-0001", observation_id="a"))
        ledger.append(simulated_evidence("run-0001", observation_id="b"))
        observations = ledger.all_observations()
    assessment = _evaluate(observations, policy=_permissive_policy())
    assert assessment.outcome is PromotionOutcome.SOURCE_INELIGIBLE


def test_imported_historical_evidence_is_source_ineligible(tmp_path):
    with _ledger(tmp_path) as ledger:
        ledger.append(imported_evidence("run-0001"))
        observations = ledger.all_observations()
    assessment = _evaluate(observations, policy=_permissive_policy())
    assert assessment.outcome is PromotionOutcome.SOURCE_INELIGIBLE


def test_mixed_live_and_simulated_evidence_is_source_ineligible(tmp_path):
    with _ledger(tmp_path) as ledger:
        ledger.append(active_protective_order("run-0001", observation_id="live"))
        ledger.append(simulated_evidence("run-0001", observation_id="sim"))
        observations = ledger.all_observations()
    assessment = _evaluate(observations, policy=_permissive_policy())
    assert assessment.outcome is PromotionOutcome.SOURCE_INELIGIBLE


def test_stale_live_evidence_is_stale(tmp_path):
    with _ledger(tmp_path) as ledger:
        ledger.append(stale_protective_order("run-0001"))
        observations = ledger.all_observations()
    assessment = _evaluate(observations, policy=_permissive_policy())
    assert assessment.outcome is PromotionOutcome.STALE_EVIDENCE


def test_evidence_older_than_maximum_age_is_stale(tmp_path):
    with _ledger(tmp_path) as ledger:
        ledger.append(
            active_protective_order(
                "run-0001", observation_id="old"
            )
        )
        observations = ledger.all_observations()
    policy = _permissive_policy(maximum_evidence_age_seconds=1.0)
    # observed_at_monotonic=100.0 in the fixture; evaluate far in the future.
    assessment = _evaluate(
        observations, policy=policy, evaluated_at_monotonic=10_000.0
    )
    assert assessment.outcome is PromotionOutcome.STALE_EVIDENCE


def test_conflicting_live_evidence_via_mixed_states(tmp_path):
    with _ledger(tmp_path) as ledger:
        ledger.append(active_protective_order("run-0001", observation_id="active"))
        ledger.append(no_protective_order("run-0001", observation_id="flat"))
        observations = ledger.all_observations()
    assessment = _evaluate(observations, policy=_permissive_policy())
    assert assessment.outcome is PromotionOutcome.CONFLICTING_EVIDENCE


def test_conflict_detected_flag_short_circuits_to_conflicting_evidence(tmp_path):
    with _ledger(tmp_path) as ledger:
        ledger.append(active_protective_order("run-0001"))
        observations = ledger.all_observations()
    assessment = _evaluate(
        observations, policy=_permissive_policy(), conflict_detected=True
    )
    assert assessment.outcome is PromotionOutcome.CONFLICTING_EVIDENCE


def test_account_fingerprint_mismatch(tmp_path):
    with _ledger(tmp_path) as ledger:
        ledger.append(mismatched_account_fingerprint("run-0001"))
        observations = ledger.all_observations()
    assessment = _evaluate(observations, policy=_permissive_policy())
    assert assessment.outcome is PromotionOutcome.ACCOUNT_MISMATCH


def test_release_mismatch(tmp_path):
    with _ledger(tmp_path) as ledger:
        ledger.append(active_protective_order("run-0001"))
        observations = ledger.all_observations()
    assessment = _evaluate(
        observations,
        policy=_permissive_policy(),
        expected_release_identity=digest("a-different-release")[:40],
    )
    assert assessment.outcome is PromotionOutcome.RELEASE_MISMATCH


def test_implementation_digest_mismatch(tmp_path):
    with _ledger(tmp_path) as ledger:
        ledger.append(mismatched_implementation_digest("run-0001"))
        observations = ledger.all_observations()
    assessment = _evaluate(observations, policy=_permissive_policy())
    assert assessment.outcome is PromotionOutcome.IMPLEMENTATION_MISMATCH


def test_incomplete_quantity_coverage(tmp_path):
    with _ledger(tmp_path) as ledger:
        ledger.append(partial_quantity_coverage("run-0001"))
        observations = ledger.all_observations()
    assessment = _evaluate(observations, policy=_permissive_policy())
    assert assessment.outcome is PromotionOutcome.COVERAGE_INCOMPLETE


def test_over_coverage_is_also_incomplete_coverage(tmp_path):
    with _ledger(tmp_path) as ledger:
        ledger.append(over_coverage("run-0001"))
        observations = ledger.all_observations()
    assessment = _evaluate(observations, policy=_permissive_policy())
    assert assessment.outcome is PromotionOutcome.COVERAGE_INCOMPLETE


def test_terminal_protection_is_protection_not_active(tmp_path):
    with _ledger(tmp_path) as ledger:
        ledger.append(terminal_protective_order("run-0001"))
        observations = ledger.all_observations()
    assessment = _evaluate(observations, policy=_permissive_policy())
    assert assessment.outcome is PromotionOutcome.PROTECTION_NOT_ACTIVE


def test_contaminated_evidence(tmp_path):
    from src.protection_observation_model import (
        AdapterIdentity,
        EvidenceSourceClass,
        ObservationContaminationStatus,
    )
    from tests._protection_observation_fixtures import make_observation

    contaminated = make_observation(
        observation_id="obs-contaminated",
        run_id="run-0001",
        broker_adapter_identity=AdapterIdentity.READ_ONLY_OBSERVATION_RUNTIME,
        evidence_source_class=EvidenceSourceClass.SIMULATED_BROKER,
        contamination_status=ObservationContaminationStatus.CONTAMINATED,
    )
    with _ledger(tmp_path) as ledger:
        ledger.append(contaminated)
        observations = ledger.all_observations()
    assessment = _evaluate(observations, policy=_permissive_policy())
    assert assessment.outcome is PromotionOutcome.CONTAMINATED_EVIDENCE


def test_privacy_unverified_evidence_cannot_reach_the_ledger_but_rubric_still_checks(
    tmp_path,
):
    """The model layer already forbids constructing an unverified-privacy
    record; the rubric independently checks anyway (defense in depth), by
    evaluating a raw, hand-built summary that bypasses the ledger."""
    from src.protection_observation_ledger import (
        LedgerAppendState,
        LedgerObservationSummary,
    )
    from src.protection_observation_model import (
        AdapterIdentity,
        EvidenceSourceClass,
        ObservationConsistencyStatus,
        ObservationContaminationStatus,
        PrivacyRedactionStatus,
        QuantityRelationship,
    )
    from src.live_order_state_model import ProtectiveCoverage

    raw = LedgerObservationSummary(
        observation_id="obs-x",
        run_id="run-0001",
        observation_sequence=1,
        recorded_sequence=1,
        observed_at_monotonic=999.0,
        release_identity=RELEASE_SHA,
        implementation_digest=EXPECTED_IMPLEMENTATION,
        account_fingerprint_reference=EXPECTED_ACCOUNT,
        broker_adapter_identity=AdapterIdentity.READ_ONLY_OBSERVATION_RUNTIME,
        evidence_source_class=EvidenceSourceClass.LIVE_READ_ONLY_BROKER_OBSERVATION,
        normalized_protection_state=ProtectiveCoverage.NOT_PROTECTED,
        quantity_relationship=QuantityRelationship.NO_POSITION,
        write_count=0,
        data_freshness=None,  # type: ignore[arg-type]
        consistency_status=ObservationConsistencyStatus.CONSISTENT,
        contamination_status=ObservationContaminationStatus.NOT_CONTAMINATED,
        privacy_redaction_status=PrivacyRedactionStatus.UNVERIFIED,
        evidence_digest=digest("x"),
        append_state=LedgerAppendState.ACCEPTED_OBSERVATION,
    )
    # data_freshness is set below to a real enum value once constructed,
    # since freshness isn't the property under test here.
    from src.read_only_broker_state import SnapshotFreshness
    raw = replace(raw, data_freshness=SnapshotFreshness.FRESH)

    assessment = _evaluate([raw], policy=_permissive_policy())
    assert assessment.outcome is PromotionOutcome.PRIVACY_UNVERIFIED


def test_nonzero_write_evidence_rejection(tmp_path):
    """Same defense-in-depth pattern: the model forbids a nonzero write
    count; the rubric independently rejects it too, tested via a raw
    summary that bypasses the model's own guard."""
    from src.protection_observation_ledger import (
        LedgerAppendState,
        LedgerObservationSummary,
    )
    from src.protection_observation_model import (
        AdapterIdentity,
        EvidenceSourceClass,
        ObservationConsistencyStatus,
        ObservationContaminationStatus,
        PrivacyRedactionStatus,
        QuantityRelationship,
    )
    from src.live_order_state_model import ProtectiveCoverage
    from src.read_only_broker_state import SnapshotFreshness

    raw = LedgerObservationSummary(
        observation_id="obs-write",
        run_id="run-0001",
        observation_sequence=1,
        recorded_sequence=1,
        observed_at_monotonic=999.0,
        release_identity=RELEASE_SHA,
        implementation_digest=EXPECTED_IMPLEMENTATION,
        account_fingerprint_reference=EXPECTED_ACCOUNT,
        broker_adapter_identity=AdapterIdentity.READ_ONLY_OBSERVATION_RUNTIME,
        evidence_source_class=EvidenceSourceClass.LIVE_READ_ONLY_BROKER_OBSERVATION,
        normalized_protection_state=ProtectiveCoverage.NOT_PROTECTED,
        quantity_relationship=QuantityRelationship.NO_POSITION,
        write_count=1,
        data_freshness=SnapshotFreshness.FRESH,
        consistency_status=ObservationConsistencyStatus.CONSISTENT,
        contamination_status=ObservationContaminationStatus.NOT_CONTAMINATED,
        privacy_redaction_status=PrivacyRedactionStatus.VERIFIED_REDACTED,
        evidence_digest=digest("write"),
        append_state=LedgerAppendState.ACCEPTED_OBSERVATION,
    )
    assessment = _evaluate([raw], policy=_permissive_policy())
    assert assessment.outcome is PromotionOutcome.NONZERO_WRITE_EVIDENCE


def test_duplicate_equivalent_records_do_not_inflate_evidence(tmp_path):
    with _ledger(tmp_path) as ledger:
        obs = active_protective_order("run-0001", observation_id="dup")
        ledger.append(obs)
        ledger.append(obs)  # DUPLICATE_EQUIVALENT, idempotent
        observations = ledger.all_observations()
    assert len(observations) == 1


def test_duplicate_conflicting_records_are_excluded_from_evaluation(tmp_path):
    with _ledger(tmp_path) as ledger:
        original = no_protective_order("run-0001", observation_id="dup")
        ledger.append(original)
        conflicting = active_protective_order("run-0001", observation_id="dup")
        ledger.append(conflicting)  # DUPLICATE_CONFLICT: no second row
        observations = ledger.all_observations()
        assert len(observations) == 1
        assert bool(ledger.conflicting_observations())
        conflict_signal = bool(ledger.conflicting_observations())
    assessment = _evaluate(
        observations, policy=_permissive_policy(), conflict_detected=conflict_signal
    )
    assert assessment.outcome is PromotionOutcome.CONFLICTING_EVIDENCE


def test_deterministic_regardless_of_ledger_insertion_order(tmp_path):
    path_a = tmp_path / "a.sqlite3"
    path_b = tmp_path / "b.sqlite3"
    records = [
        active_protective_order("run-a", observation_id="obs-1"),
        active_protective_order("run-b", observation_id="obs-2"),
    ]
    with ProtectionObservationLedger(path_a, "run-a") as ledger:
        ledger.append(records[0])
    with ProtectionObservationLedger(path_a, "run-b") as ledger:
        ledger.append(records[1])
        forward = ledger.all_observations()

    with ProtectionObservationLedger(path_b, "run-b") as ledger:
        ledger.append(records[1])
    with ProtectionObservationLedger(path_b, "run-a") as ledger:
        ledger.append(records[0])
        backward = list(reversed(ledger.all_observations()))

    policy = _permissive_policy()
    forward_assessment = _evaluate(forward, policy=policy)
    backward_assessment = _evaluate(backward, policy=policy)
    assert forward_assessment.outcome == backward_assessment.outcome
    assert (
        forward_assessment.distinct_run_count
        == backward_assessment.distinct_run_count
    )


def test_policy_version_change_changes_reported_policy_version(tmp_path):
    with _ledger(tmp_path) as ledger:
        ledger.append(no_protective_order("run-0001"))
        observations = ledger.all_observations()
    policy_a = _permissive_policy(policy_version="v-a")
    policy_b = _permissive_policy(policy_version="v-b")
    assessment_a = _evaluate(observations, policy=policy_a)
    assessment_b = _evaluate(observations, policy=policy_b)
    assert assessment_a.policy_version == "v-a"
    assert assessment_b.policy_version == "v-b"
    assert assessment_a.outcome == assessment_b.outcome


# --------------------------------------------------------------------------
# Promotion/authority guarantees.
# --------------------------------------------------------------------------


def test_promotion_disabled_default_never_reports_eligible_directly(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    for index, run_id in enumerate(("run-a", "run-b", "run-c")):
        with ProtectionObservationLedger(path, run_id) as ledger:
            ledger.append(
                active_protective_order(
                    run_id, observation_id=f"obs-{index}",
                    observed_at_monotonic=100.0 + index,
                )
            )
    with ProtectionObservationLedger(path, "run-c") as ledger:
        observations = ledger.all_observations()
    assessment = _evaluate(observations)  # default policy: promotion_enabled=False
    assert assessment.outcome is PromotionOutcome.PROMOTION_NOT_PERMITTED
    assert assessment.promotion_permitted is False


def test_automatic_promotion_prohibited_structurally(tmp_path):
    for policy_kwargs in (
        {"automatic_promotion_permitted": False},
    ):
        _permissive_policy(**policy_kwargs)  # does not raise
    with pytest.raises(ProtectionPromotionPolicyError):
        _permissive_policy(automatic_promotion_permitted=True)


def test_independent_review_required_flows_through_to_assessment(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    for index, run_id in enumerate(("run-a", "run-b")):
        with ProtectionObservationLedger(path, run_id) as ledger:
            ledger.append(
                active_protective_order(
                    run_id, observation_id=f"obs-{index}",
                    observed_at_monotonic=100.0 + index,
                )
            )
    with ProtectionObservationLedger(path, "run-b") as ledger:
        observations = ledger.all_observations()
    policy = _permissive_policy(independent_review_required=True)
    assessment = _evaluate(observations, policy=policy)
    assert assessment.independent_review_required is True
    assert assessment.outcome is PromotionOutcome.REVIEW_REQUIRED
    assert assessment.promotion_permitted is False


def test_promotion_assessment_cannot_be_constructed_with_promotion_permitted():
    from src.protection_promotion_policy import PromotionAssessment

    with pytest.raises(ProtectionPromotionPolicyError):
        PromotionAssessment(
            outcome=PromotionOutcome.ELIGIBLE_FOR_REVIEW,
            policy_version="v1",
            evaluated_at_monotonic=1.0,
            observation_count=1,
            protective_observation_count=1,
            distinct_run_count=1,
            distinct_observation_time_count=1,
            independent_review_required=False,
            promotion_permitted=True,
        )


# --------------------------------------------------------------------------
# Evaluation is pure: never mutates the ledger or any authority module.
# --------------------------------------------------------------------------


def test_evaluation_does_not_mutate_the_ledger(tmp_path):
    with _ledger(tmp_path) as ledger:
        ledger.append(active_protective_order("run-0001"))
        before = ledger.all_observations()
        _evaluate(before, policy=_permissive_policy())
        after = ledger.all_observations()
    assert before == after


def test_evaluation_does_not_mutate_any_authority_module(tmp_path):
    before_gate = ACTIVATION_APPROVALS
    before_states = ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES
    with _ledger(tmp_path) as ledger:
        ledger.append(active_protective_order("run-0001"))
        observations = ledger.all_observations()
    _evaluate(observations, policy=_permissive_policy())
    assert ACTIVATION_APPROVALS == before_gate == frozenset()
    assert (
        ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES == before_states == frozenset()
    )


def test_policy_module_has_no_authority_setters():
    import src.protection_promotion_policy as policy_module

    forbidden = (
        "protection_grounded",
        "activation_approved",
        "permits_automated_action",
        "live_ready",
        "unattended_ready",
    )
    for name in forbidden:
        assert not hasattr(policy_module, name)
