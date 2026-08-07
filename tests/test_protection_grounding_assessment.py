"""Protection-grounding assessment engine (M13 Issue 4).

Every test uses `tmp_path` for the ledger. Nothing here touches a broker,
the network, or a real clock. The engine never mutates the ledger or any
authority module -- verified directly below.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from src.execution_gates import ACTIVATION_APPROVALS
from src.live_order_state_model import ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES
from src.protection_grounding_assessment import (
    ObservationDisposition,
    ObservationDispositionReason,
    OperatorReviewContext,
    ProtectionGroundingAssessmentError,
    evaluate_protection_grounding,
)
from src.protection_observation_ledger import ProtectionObservationLedger
from src.protection_promotion_policy import (
    PromotionOutcome,
    ProtectionPromotionPolicy,
)
from tests._protection_observation_fixtures import (
    RELEASE_SHA,
    active_protective_order,
    digest,
    imported_evidence,
    make_observation,
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


def _assess(observations, **overrides):
    kwargs = dict(
        evaluated_at_monotonic=1000.0,
        expected_release_identity=RELEASE_SHA,
        expected_implementation_digest=EXPECTED_IMPLEMENTATION,
        expected_account_fingerprint_reference=EXPECTED_ACCOUNT,
        operator_presence_attested=True,
    )
    kwargs.update(overrides)
    return evaluate_protection_grounding(observations, **kwargs)


# --------------------------------------------------------------------------
# 1-4: core evidentiary scenarios.
# --------------------------------------------------------------------------


def test_zero_observations(tmp_path):
    assessment = _assess([])
    assert assessment.recommendation is PromotionOutcome.INSUFFICIENT_EVIDENCE
    assert assessment.candidate_observation_ids == ()
    assert assessment.included_observation_ids == ()


def test_only_no_protection_observations(tmp_path):
    with _ledger(tmp_path) as ledger:
        ledger.append(no_protective_order("run-0001", observation_id="a"))
        ledger.append(no_protective_order("run-0001", observation_id="b"))
        observations = ledger.all_observations()
    assessment = _assess(observations, policy=_permissive_policy())
    assert assessment.recommendation is PromotionOutcome.PROTECTION_NOT_ACTIVE
    assert assessment.informational_observation_ids == ("a", "b")
    assert assessment.included_observation_ids == ()
    assert assessment.excluded_observation_ids == ()


def test_one_eligible_live_observation(tmp_path):
    with _ledger(tmp_path) as ledger:
        ledger.append(active_protective_order("run-0001", observation_id="a"))
        observations = ledger.all_observations()
    assessment = _assess(observations, policy=_permissive_policy())
    assert assessment.included_observation_ids == ("a",)
    # One run: independence analysis fails under the permissive policy's
    # minimum of 2.
    assert assessment.independence_analysis.passed is False
    assert assessment.recommendation is PromotionOutcome.INSUFFICIENT_EVIDENCE


def test_multiple_eligible_live_observations(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    for index, run_id in enumerate(("run-a", "run-b")):
        with ProtectionObservationLedger(path, run_id) as ledger:
            ledger.append(
                active_protective_order(
                    run_id,
                    observation_id=f"obs-{index}",
                    observed_at_monotonic=100.0 + index,
                )
            )
    with ProtectionObservationLedger(path, "run-b") as ledger:
        observations = ledger.all_observations()
    assessment = _assess(observations, policy=_permissive_policy())
    assert assessment.included_observation_ids == ("obs-0", "obs-1")
    assert assessment.independence_analysis.passed is True
    assert assessment.recommendation is PromotionOutcome.ELIGIBLE_FOR_REVIEW
    assert assessment.promotion_permitted is False
    assert assessment.protection_grounded is False


# --------------------------------------------------------------------------
# 5-6: duplicate handling.
# --------------------------------------------------------------------------


def test_duplicate_equivalent_observations_do_not_inflate_counts(tmp_path):
    with _ledger(tmp_path) as ledger:
        obs = active_protective_order("run-0001", observation_id="dup")
        ledger.append(obs)
        ledger.append(obs)
        observations = ledger.all_observations()
    assert len(observations) == 1
    assessment = _assess(observations, policy=_permissive_policy())
    assert assessment.qualifying_observation_count == 1


def test_duplicate_conflict_blocks_qualification(tmp_path):
    with _ledger(tmp_path) as ledger:
        original = no_protective_order("run-0001", observation_id="dup")
        ledger.append(original)
        conflicting = active_protective_order("run-0001", observation_id="dup")
        ledger.append(conflicting)
        observations = ledger.all_observations()
        conflict_detected = bool(ledger.conflicting_observations())
    assessment = _assess(
        observations, policy=_permissive_policy(), conflict_detected=conflict_detected
    )
    assert assessment.recommendation is PromotionOutcome.CONFLICTING_EVIDENCE
    assert assessment.conflicting_evidence_analysis.conflict_detected is True
    assert assessment.conflicting_evidence_analysis.passed is False


# --------------------------------------------------------------------------
# 7-10: source-class scenarios.
# --------------------------------------------------------------------------


def test_simulated_only_evidence(tmp_path):
    with _ledger(tmp_path) as ledger:
        ledger.append(simulated_evidence("run-0001", observation_id="a"))
        observations = ledger.all_observations()
    assessment = _assess(observations, policy=_permissive_policy())
    assert assessment.source_authority_analysis.passed is False
    assert assessment.source_authority_analysis.failing_observation_ids == ("a",)
    assert assessment.recommendation is PromotionOutcome.SOURCE_INELIGIBLE


def test_test_fixture_only_evidence(tmp_path):
    from src.protection_observation_model import (
        AdapterIdentity,
        EvidenceSourceClass,
    )

    fixture_obs = make_observation(
        observation_id="obs-fixture",
        run_id="run-0001",
        evidence_source_class=EvidenceSourceClass.TEST_FIXTURE,
        broker_adapter_identity=AdapterIdentity.TEST_DOUBLE,
    )
    with _ledger(tmp_path) as ledger:
        ledger.append(fixture_obs)
        observations = ledger.all_observations()
    assessment = _assess(observations, policy=_permissive_policy())
    assert assessment.recommendation is PromotionOutcome.SOURCE_INELIGIBLE


def test_imported_only_evidence(tmp_path):
    with _ledger(tmp_path) as ledger:
        ledger.append(imported_evidence("run-0001"))
        observations = ledger.all_observations()
    assessment = _assess(observations, policy=_permissive_policy())
    assert assessment.recommendation is PromotionOutcome.SOURCE_INELIGIBLE


def test_mixed_live_and_simulated_evidence(tmp_path):
    with _ledger(tmp_path) as ledger:
        ledger.append(active_protective_order("run-0001", observation_id="live"))
        ledger.append(simulated_evidence("run-0001", observation_id="sim"))
        observations = ledger.all_observations()
    assessment = _assess(observations, policy=_permissive_policy())
    assert assessment.recommendation is PromotionOutcome.SOURCE_INELIGIBLE
    assert assessment.exclusion_reasons["sim"] == "SOURCE_INELIGIBLE"


# --------------------------------------------------------------------------
# 11-12: staleness and conflict.
# --------------------------------------------------------------------------


def test_stale_evidence(tmp_path):
    with _ledger(tmp_path) as ledger:
        ledger.append(stale_protective_order("run-0001", observation_id="a"))
        observations = ledger.all_observations()
    assessment = _assess(observations, policy=_permissive_policy())
    assert assessment.freshness_analysis.passed is False
    assert assessment.freshness_analysis.failing_observation_ids == ("a",)
    assert assessment.recommendation is PromotionOutcome.STALE_EVIDENCE


def test_conflicting_live_evidence(tmp_path):
    with _ledger(tmp_path) as ledger:
        ledger.append(active_protective_order("run-0001", observation_id="active"))
        ledger.append(no_protective_order("run-0001", observation_id="flat"))
        observations = ledger.all_observations()
    assessment = _assess(observations, policy=_permissive_policy())
    assert assessment.recommendation is PromotionOutcome.CONFLICTING_EVIDENCE
    assert assessment.conflicting_evidence_analysis.mixed_protective_and_flat is True


# --------------------------------------------------------------------------
# 13-15: identity mismatches.
# --------------------------------------------------------------------------


def test_account_mismatch(tmp_path):
    with _ledger(tmp_path) as ledger:
        ledger.append(mismatched_account_fingerprint("run-0001", observation_id="a"))
        observations = ledger.all_observations()
    assessment = _assess(observations, policy=_permissive_policy())
    assert assessment.account_consistency_analysis.passed is False
    assert assessment.recommendation is PromotionOutcome.ACCOUNT_MISMATCH


def test_release_mismatch(tmp_path):
    with _ledger(tmp_path) as ledger:
        ledger.append(active_protective_order("run-0001", observation_id="a"))
        observations = ledger.all_observations()
    assessment = _assess(
        observations,
        policy=_permissive_policy(),
        expected_release_identity=digest("different-release")[:40],
    )
    assert assessment.release_consistency_analysis.passed is False
    assert assessment.recommendation is PromotionOutcome.RELEASE_MISMATCH


def test_implementation_mismatch(tmp_path):
    with _ledger(tmp_path) as ledger:
        ledger.append(
            mismatched_implementation_digest("run-0001", observation_id="a")
        )
        observations = ledger.all_observations()
    assessment = _assess(observations, policy=_permissive_policy())
    assert assessment.implementation_consistency_analysis.passed is False
    assert assessment.recommendation is PromotionOutcome.IMPLEMENTATION_MISMATCH


# --------------------------------------------------------------------------
# 16-21: coverage / terminal / contamination / privacy / write.
# --------------------------------------------------------------------------


def test_incomplete_quantity_coverage(tmp_path):
    with _ledger(tmp_path) as ledger:
        ledger.append(partial_quantity_coverage("run-0001", observation_id="a"))
        observations = ledger.all_observations()
    assessment = _assess(observations, policy=_permissive_policy())
    assert assessment.quantity_coverage_analysis.passed is False
    assert assessment.recommendation is PromotionOutcome.COVERAGE_INCOMPLETE


def test_over_coverage(tmp_path):
    with _ledger(tmp_path) as ledger:
        ledger.append(over_coverage("run-0001", observation_id="a"))
        observations = ledger.all_observations()
    assessment = _assess(observations, policy=_permissive_policy())
    assert assessment.quantity_coverage_analysis.passed is False
    assert assessment.recommendation is PromotionOutcome.COVERAGE_INCOMPLETE


def test_terminal_protection(tmp_path):
    with _ledger(tmp_path) as ledger:
        ledger.append(terminal_protective_order("run-0001", observation_id="a"))
        observations = ledger.all_observations()
    assessment = _assess(observations, policy=_permissive_policy())
    assert assessment.recommendation is PromotionOutcome.PROTECTION_NOT_ACTIVE
    assert assessment.informational_observation_ids == ("a",)


def test_contaminated_evidence(tmp_path):
    from src.protection_observation_model import (
        AdapterIdentity,
        EvidenceSourceClass,
        ObservationContaminationStatus,
    )

    contaminated = make_observation(
        observation_id="a",
        run_id="run-0001",
        broker_adapter_identity=AdapterIdentity.READ_ONLY_OBSERVATION_RUNTIME,
        evidence_source_class=EvidenceSourceClass.SIMULATED_BROKER,
        contamination_status=ObservationContaminationStatus.CONTAMINATED,
    )
    with _ledger(tmp_path) as ledger:
        ledger.append(contaminated)
        observations = ledger.all_observations()
    assessment = _assess(observations, policy=_permissive_policy())
    assert assessment.contamination_analysis.passed is False
    assert assessment.recommendation is PromotionOutcome.CONTAMINATED_EVIDENCE


def test_privacy_unverified_evidence(tmp_path):
    from dataclasses import replace as dc_replace

    from src.live_order_state_model import ProtectiveCoverage
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
    from src.read_only_broker_state import SnapshotFreshness

    raw = LedgerObservationSummary(
        observation_id="a",
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
        data_freshness=SnapshotFreshness.FRESH,
        consistency_status=ObservationConsistencyStatus.CONSISTENT,
        contamination_status=ObservationContaminationStatus.NOT_CONTAMINATED,
        privacy_redaction_status=PrivacyRedactionStatus.UNVERIFIED,
        evidence_digest=digest("privacy-unverified"),
        append_state=LedgerAppendState.ACCEPTED_OBSERVATION,
    )
    assessment = _assess([raw], policy=_permissive_policy())
    assert assessment.privacy_analysis.passed is False
    assert assessment.recommendation is PromotionOutcome.PRIVACY_UNVERIFIED


def test_nonzero_write_evidence(tmp_path):
    from src.live_order_state_model import ProtectiveCoverage
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
    from src.read_only_broker_state import SnapshotFreshness

    raw = LedgerObservationSummary(
        observation_id="a",
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
        evidence_digest=digest("nonzero-write"),
        append_state=LedgerAppendState.ACCEPTED_OBSERVATION,
    )
    assessment = _assess([raw], policy=_permissive_policy())
    assert assessment.zero_write_analysis.passed is False
    assert assessment.recommendation is PromotionOutcome.NONZERO_WRITE_EVIDENCE


# --------------------------------------------------------------------------
# 22-25: promotion/review/authority guarantees.
# --------------------------------------------------------------------------


def test_promotion_disabled(tmp_path):
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
    assessment = _assess(observations)  # default policy: promotion disabled
    assert assessment.recommendation is PromotionOutcome.PROMOTION_NOT_PERMITTED
    assert assessment.promotion_permitted is False


def test_automatic_promotion_prohibited():
    with pytest.raises(Exception):
        _permissive_policy(automatic_promotion_permitted=True)


def test_independent_review_required(tmp_path):
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
    assessment = _assess(observations, policy=policy)
    assert assessment.recommendation is PromotionOutcome.REVIEW_REQUIRED
    assert assessment.promotion_permitted is False


def test_evidence_technically_qualifying_but_promotion_not_permitted(tmp_path):
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
    # Default policy: sufficient evidence exists (3 runs), but promotion
    # remains disabled -- qualification and permission are distinct.
    assessment = _assess(observations)
    assert assessment.qualifying_observation_count == 3
    assert assessment.independence_analysis.passed is True
    assert assessment.recommendation is PromotionOutcome.PROMOTION_NOT_PERMITTED
    assert assessment.promotion_permitted is False
    assert assessment.protection_grounded is False


# --------------------------------------------------------------------------
# 26-34: determinism.
# --------------------------------------------------------------------------


def test_deterministic_output_across_repeated_evaluation(tmp_path):
    with _ledger(tmp_path) as ledger:
        ledger.append(active_protective_order("run-0001", observation_id="a"))
        observations = ledger.all_observations()
    first = _assess(observations, policy=_permissive_policy())
    second = _assess(observations, policy=_permissive_policy())
    assert first.assessment_digest == second.assessment_digest
    assert first.recommendation == second.recommendation


def test_deterministic_output_across_ledger_insertion_order(tmp_path):
    path_a = tmp_path / "a.sqlite3"
    path_b = tmp_path / "b.sqlite3"
    obs_1 = active_protective_order(
        "run-a", observation_id="obs-1", observed_at_monotonic=100.0
    )
    obs_2 = active_protective_order(
        "run-b", observation_id="obs-2", observed_at_monotonic=101.0
    )
    with ProtectionObservationLedger(path_a, "run-a") as ledger:
        ledger.append(obs_1)
    with ProtectionObservationLedger(path_a, "run-b") as ledger:
        ledger.append(obs_2)
        forward = ledger.all_observations()

    with ProtectionObservationLedger(path_b, "run-b") as ledger:
        ledger.append(obs_2)
    with ProtectionObservationLedger(path_b, "run-a") as ledger:
        ledger.append(obs_1)
        backward = list(reversed(ledger.all_observations()))

    policy = _permissive_policy()
    first = _assess(forward, policy=policy)
    second = _assess(backward, policy=policy)
    assert first.assessment_digest == second.assessment_digest


def test_stable_assessment_digest():
    obs = active_protective_order("run-0001", observation_id="a")
    first = _assess([obs], policy=_permissive_policy())
    second = _assess([obs], policy=_permissive_policy())
    assert first.assessment_digest == second.assessment_digest
    assert len(first.assessment_digest) == 64


def test_policy_version_change_changes_digest():
    obs = active_protective_order("run-0001", observation_id="a")
    a = _assess([obs], policy=_permissive_policy(policy_version="v-a"))
    b = _assess([obs], policy=_permissive_policy(policy_version="v-b"))
    assert a.assessment_digest != b.assessment_digest


def test_evidence_set_change_changes_digest():
    obs_a = active_protective_order("run-0001", observation_id="a")
    obs_b = active_protective_order("run-0001", observation_id="b")
    first = _assess([obs_a], policy=_permissive_policy())
    second = _assess([obs_a, obs_b], policy=_permissive_policy())
    assert first.assessment_digest != second.assessment_digest


def test_release_identity_change_changes_digest():
    obs = active_protective_order("run-0001", observation_id="a")
    first = _assess([obs], policy=_permissive_policy())
    second = _assess(
        [obs],
        policy=_permissive_policy(),
        expected_release_identity=digest("other-release")[:40],
    )
    assert first.assessment_digest != second.assessment_digest


def test_implementation_digest_change_changes_digest():
    obs = active_protective_order("run-0001", observation_id="a")
    first = _assess([obs], policy=_permissive_policy())
    second = _assess(
        [obs],
        policy=_permissive_policy(),
        expected_implementation_digest=digest("other-implementation"),
    )
    assert first.assessment_digest != second.assessment_digest


def test_account_fingerprint_change_changes_digest():
    obs = active_protective_order("run-0001", observation_id="a")
    first = _assess([obs], policy=_permissive_policy())
    second = _assess(
        [obs],
        policy=_permissive_policy(),
        expected_account_fingerprint_reference=digest("other-account"),
    )
    assert first.assessment_digest != second.assessment_digest


def test_explicit_freshness_time_input_changes_result_deterministically(tmp_path):
    with _ledger(tmp_path) as ledger:
        ledger.append(active_protective_order("run-0001", observation_id="a"))
        observations = ledger.all_observations()
    near = _assess(
        observations,
        policy=_permissive_policy(maximum_evidence_age_seconds=10.0),
        evaluated_at_monotonic=105.0,
    )
    far = _assess(
        observations,
        policy=_permissive_policy(maximum_evidence_age_seconds=10.0),
        evaluated_at_monotonic=10_000.0,
    )
    assert near.freshness_analysis.passed is True
    assert far.freshness_analysis.passed is False
    assert near.assessment_digest != far.assessment_digest


# --------------------------------------------------------------------------
# 35-37: purity and no broker access.
# --------------------------------------------------------------------------


def test_assessment_does_not_mutate_ledger(tmp_path):
    with _ledger(tmp_path) as ledger:
        ledger.append(active_protective_order("run-0001"))
        before = ledger.all_observations()
        _assess(before, policy=_permissive_policy())
        after = ledger.all_observations()
    assert before == after


def test_assessment_does_not_mutate_authority_state(tmp_path):
    before_gate = ACTIVATION_APPROVALS
    before_states = ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES
    with _ledger(tmp_path) as ledger:
        ledger.append(active_protective_order("run-0001"))
        observations = ledger.all_observations()
    _assess(observations, policy=_permissive_policy())
    assert ACTIVATION_APPROVALS == before_gate == frozenset()
    assert (
        ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES == before_states == frozenset()
    )


def test_no_broker_imports_or_calls():
    import ast
    import inspect

    import src.protection_grounding_assessment as engine_module

    source = inspect.getsource(engine_module)
    tree = ast.parse(source)
    forbidden_modules = {"src.robinhood_client", "src.robinhood_commissioning_adapter"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in forbidden_modules
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module not in forbidden_modules
    assert "call_tool" not in source
    assert "session.session" not in source


# --------------------------------------------------------------------------
# 38-40: reason clarity and advisory-only guarantee.
# --------------------------------------------------------------------------


def test_excluded_evidence_receives_explicit_reasons(tmp_path):
    with _ledger(tmp_path) as ledger:
        ledger.append(mismatched_account_fingerprint("run-0001", observation_id="a"))
        observations = ledger.all_observations()
    assessment = _assess(observations, policy=_permissive_policy())
    assert assessment.exclusion_reasons["a"] == "ACCOUNT_MISMATCH"
    assert "a" in assessment.excluded_observation_ids


def test_included_evidence_receives_explicit_qualification_analysis(tmp_path):
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
    assessment = _assess(observations, policy=_permissive_policy())
    assert assessment.included_observation_ids == ("obs-0", "obs-1")
    assert assessment.source_authority_analysis.passed is True
    assert assessment.freshness_analysis.passed is True
    assert assessment.quantity_coverage_analysis.passed is True
    assert assessment.independence_analysis.passed is True
    for oid in assessment.included_observation_ids:
        assert oid not in assessment.exclusion_reasons


def test_recommendation_remains_advisory_only(tmp_path):
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
    policy = _permissive_policy(independent_review_required=False)
    assessment = _assess(observations, policy=policy)
    assert assessment.recommendation is PromotionOutcome.ELIGIBLE_FOR_REVIEW
    assert assessment.promotion_permitted is False
    assert assessment.protection_grounded is False


def test_assessment_cannot_be_constructed_with_promotion_permitted():
    from src.protection_grounding_assessment import ProtectionGroundingAssessment
    from src.protection_grounding_assessment import (
        ActiveStateAnalysis,
        ConflictingEvidenceAnalysis,
        DimensionAnalysis,
        IndependenceAnalysis,
    )

    base_dimension = DimensionAnalysis(passed=True, checked_count=0, failing_observation_ids=())
    with pytest.raises(ProtectionGroundingAssessmentError):
        ProtectionGroundingAssessment(
            assessment_id="x",
            assessment_version="v1",
            generated_at_monotonic=1.0,
            policy_version="v1",
            release_identity=RELEASE_SHA,
            implementation_digest=EXPECTED_IMPLEMENTATION,
            account_fingerprint_reference=EXPECTED_ACCOUNT,
            candidate_observation_ids=(),
            included_observation_ids=(),
            excluded_observation_ids=(),
            informational_observation_ids=(),
            exclusion_reasons={},
            source_authority_analysis=base_dimension,
            freshness_analysis=base_dimension,
            account_consistency_analysis=base_dimension,
            release_consistency_analysis=base_dimension,
            implementation_consistency_analysis=base_dimension,
            privacy_analysis=base_dimension,
            zero_write_analysis=base_dimension,
            quantity_coverage_analysis=base_dimension,
            active_state_analysis=ActiveStateAnalysis(
                passed=True, protective_observation_count=0, flat_observation_count=0
            ),
            conflicting_evidence_analysis=ConflictingEvidenceAnalysis(
                passed=True, conflict_detected=False, mixed_protective_and_flat=False
            ),
            contamination_analysis=base_dimension,
            independence_analysis=IndependenceAnalysis(
                passed=True,
                distinct_run_count=0,
                distinct_observation_time_count=0,
                minimum_required_runs=1,
                minimum_required_observation_times=1,
            ),
            distinct_run_count=0,
            distinct_observation_time_count=0,
            broker_authoritative_observation_count=0,
            qualifying_observation_count=0,
            recommendation=PromotionOutcome.INSUFFICIENT_EVIDENCE,
            reason_codes=(),
            operator_review_context=None,
            assessment_digest="x" * 64,
            promotion_permitted=True,
        )


def test_operator_review_context_has_no_approval_field():
    context = OperatorReviewContext(review_requested=True)
    assert not hasattr(context, "approved")
    assert not hasattr(context, "approval")
    assert not hasattr(context, "activation_state")


def test_operator_review_context_rejects_non_hex_references():
    with pytest.raises(ProtectionGroundingAssessmentError):
        OperatorReviewContext(reviewer_reference="not-a-hex-digest")


def test_assessment_engine_module_has_no_authority_setters():
    import src.protection_grounding_assessment as engine_module

    forbidden = (
        "protection_grounded",
        "activation_approved",
        "permits_automated_action",
        "live_ready",
        "unattended_ready",
    )
    for name in forbidden:
        assert not hasattr(engine_module, name)
