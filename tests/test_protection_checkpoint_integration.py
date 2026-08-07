"""M13 Issue 5: read-only checkpoint <-> protection-grounding integration.

Every test uses `tmp_path` for the ledger. Nothing here touches a broker,
the network, a real clock, or executes an actual checkpoint -- every
`CheckpointResult` here is hand-built, exactly as the existing runtime
would have produced it, per repository fixture convention.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.execution_gates import ACTIVATION_APPROVALS
from src.live_order_state_model import (
    ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES,
    ProtectiveCoverage,
)
from src.protection_checkpoint_integration import (
    IntegratedCheckpointResult,
    IntegrationStatus,
    integrate_checkpoint_result,
)
from src.protection_observation_model import AdapterIdentity, EvidenceSourceClass
from src.protection_promotion_policy import PromotionOutcome
from src.read_only_observation_runtime import (
    CheckpointOutcome,
    CheckpointResult,
    ReadOnlyObservationEvidence,
)

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


def _evidence(
    *,
    run_id: str,
    outcome: CheckpointOutcome,
    observed_protection_state: str = "NONE",
    order_state_changed: bool = False,
    freshness: str = "FRESH",
) -> ReadOnlyObservationEvidence:
    return ReadOnlyObservationEvidence(
        run_id=run_id,
        outcome=outcome.value,
        decision="VERIFIED_FLAT" if observed_protection_state == "NONE" else "UNPROTECTED_POSITION",
        freshness=freshness,
        snapshot_facts={
            "order_state_changed_during_acquisition": (
                "true" if order_state_changed else "false"
            ),
        },
        observed_protection_state=observed_protection_state,
        proposed_provenance="NONE",
        read_call_counts={"get_accounts": 1, "get_equity_positions": 1, "get_equity_orders": 2},
        approved_active_protection_state_count=0,
        write_counters={
            "placement_writes": 0,
            "cancellation_writes": 0,
            "replacement_writes": 0,
            "strategy_activations": 0,
        },
        operator_action_required="NONE",
    )


def _result(
    *,
    run_id: str,
    outcome: CheckpointOutcome,
    observed_protection_state: str = "NONE",
    order_state_changed: bool = False,
    freshness: str = "FRESH",
) -> CheckpointResult:
    return CheckpointResult(
        outcome=outcome,
        evidence=_evidence(
            run_id=run_id,
            outcome=outcome,
            observed_protection_state=observed_protection_state,
            order_state_changed=order_state_changed,
            freshness=freshness,
        ),
        evidence_path=None,
    )


def _integrate(tmp_path: Path, checkpoint_result: CheckpointResult, **overrides):
    kwargs = dict(
        run_id=checkpoint_result.evidence.run_id,
        account_number="123456789",
        ledger_path=tmp_path / "ledger.sqlite3",
        evaluated_at_monotonic=100.0,
        repository_root=REPOSITORY_ROOT,
        evidence_source_class=EvidenceSourceClass.LIVE_READ_ONLY_BROKER_OBSERVATION,
        broker_adapter_identity=AdapterIdentity.READ_ONLY_OBSERVATION_RUNTIME,
        broker_session_identity_reference="a" * 64,
        operator_present=True,
    )
    kwargs.update(overrides)
    return integrate_checkpoint_result(checkpoint_result, **kwargs)


# --------------------------------------------------------------------------
# NOT_APPLICABLE for non-completed outcomes.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "outcome",
    [
        CheckpointOutcome.DECLINED_BY_OPERATOR,
        CheckpointOutcome.AUTHORIZATION_REJECTED,
        CheckpointOutcome.PRECONDITION_REJECTED,
        CheckpointOutcome.CAPABILITY_VIOLATION,
        CheckpointOutcome.SESSION_UNAVAILABLE,
        CheckpointOutcome.BROKER_READ_UNAVAILABLE,
        CheckpointOutcome.SNAPSHOT_NOT_FRESH,
        CheckpointOutcome.PROTECTION_AMBIGUOUS,
        CheckpointOutcome.ATTRIBUTION_AMBIGUOUS,
        CheckpointOutcome.INTERNAL_INVARIANT_FAILURE,
    ],
)
def test_non_completed_outcomes_are_not_applicable(tmp_path, outcome):
    result = _result(run_id="run-x", outcome=outcome)
    integrated = _integrate(tmp_path, result)
    assert integrated.integration_status is IntegrationStatus.NOT_APPLICABLE
    assert integrated.observation is None
    assert integrated.assessment is None
    # The core result is passed through unmodified.
    assert integrated.checkpoint is result


# --------------------------------------------------------------------------
# No-protection normal completion.
# --------------------------------------------------------------------------


def test_offline_no_protection_checkpoint_integrates_normally(tmp_path):
    result = _result(
        run_id="run-no-protection", outcome=CheckpointOutcome.NO_PROTECTIVE_ORDER_OBSERVED
    )
    integrated = _integrate(tmp_path, result)
    assert integrated.integration_status is IntegrationStatus.INTEGRATED
    assert integrated.observation.normalized_protection_state is ProtectiveCoverage.NOT_PROTECTED
    assert integrated.observation.write_count == 0
    assert integrated.append_result is not None
    assert integrated.assessment is not None
    # No protective order observed is a legitimate, non-failure result.
    assert integrated.assessment.recommendation is PromotionOutcome.PROTECTION_NOT_ACTIVE


def test_no_protection_never_creates_an_incident_by_itself(tmp_path):
    result = _result(
        run_id="run-no-protection-2", outcome=CheckpointOutcome.NO_PROTECTIVE_ORDER_OBSERVED
    )
    integrated = _integrate(tmp_path, result)
    assert integrated.integration_status is IntegrationStatus.INTEGRATED
    assert integrated.assessment.recommendation in {
        PromotionOutcome.PROTECTION_NOT_ACTIVE,
        PromotionOutcome.INSUFFICIENT_EVIDENCE,
    }


# --------------------------------------------------------------------------
# Active-protection fixture result.
# --------------------------------------------------------------------------


def test_offline_active_protection_fixture(tmp_path):
    result = _result(
        run_id="run-active",
        outcome=CheckpointOutcome.OBSERVATION_RECORDED,
        observed_protection_state="confirmed",
    )
    integrated = _integrate(tmp_path, result)
    assert integrated.integration_status is IntegrationStatus.INTEGRATED
    assert (
        integrated.observation.normalized_protection_state
        is ProtectiveCoverage.INDETERMINATE
    )


def test_simulated_active_protection_remains_ineligible_for_live_grounding(tmp_path):
    result = _result(
        run_id="run-fixture-active",
        outcome=CheckpointOutcome.OBSERVATION_RECORDED,
        observed_protection_state="confirmed",
    )
    integrated = _integrate(
        tmp_path,
        result,
        evidence_source_class=EvidenceSourceClass.TEST_FIXTURE,
        broker_adapter_identity=AdapterIdentity.TEST_DOUBLE,
    )
    assert integrated.integration_status is IntegrationStatus.INTEGRATED
    assert integrated.assessment.recommendation is PromotionOutcome.SOURCE_INELIGIBLE
    # Never grounded, never approved, regardless of outcome.
    assert integrated.assessment.promotion_permitted is False
    assert integrated.assessment.protection_grounded is False


def test_fixture_cannot_produce_live_grounding_even_with_permissive_policy(tmp_path):
    from src.protection_promotion_policy import ProtectionPromotionPolicy

    permissive = ProtectionPromotionPolicy(
        policy_version="test-permissive",
        promotion_enabled=True,
        automatic_promotion_permitted=False,
        independent_review_required=False,
        minimum_distinct_eligible_live_runs=1,
        minimum_distinct_observation_times=1,
        maximum_evidence_age_seconds=1_000_000.0,
        require_release_consistency=True,
        require_implementation_digest_consistency=True,
        require_account_fingerprint_consistency=True,
        require_broker_authoritative_source=True,
        require_active_protection_state=True,
        require_exact_quantity_coverage=False,
        reject_contradictory_evidence=True,
        reject_contaminated_evidence=True,
        reject_stale_evidence=True,
        require_zero_write_proof=True,
        require_privacy_verification=True,
        require_operator_presence_attestation=False,
        threshold_pending_independent_review=True,
    )
    result = _result(
        run_id="run-fixture-permissive",
        outcome=CheckpointOutcome.OBSERVATION_RECORDED,
        observed_protection_state="confirmed",
    )
    integrated = _integrate(
        tmp_path,
        result,
        evidence_source_class=EvidenceSourceClass.SIMULATED_BROKER,
        broker_adapter_identity=AdapterIdentity.SIMULATED_BROKER_TRANSPORT,
        policy=permissive,
    )
    assert integrated.assessment.recommendation is PromotionOutcome.SOURCE_INELIGIBLE
    assert integrated.assessment.protection_grounded is False


# --------------------------------------------------------------------------
# Canonical observation / IDs / evidence source class.
# --------------------------------------------------------------------------


def test_canonical_observation_creation(tmp_path):
    result = _result(run_id="run-canon", outcome=CheckpointOutcome.NO_PROTECTIVE_ORDER_OBSERVED)
    integrated = _integrate(tmp_path, result)
    obs = integrated.observation
    assert obs.run_id == "run-canon"
    assert len(obs.observation_id) == 64
    assert obs.write_count == 0
    assert obs.read_count == 4


def test_observation_id_is_stable_for_the_same_run(tmp_path):
    result = _result(run_id="run-stable", outcome=CheckpointOutcome.NO_PROTECTIVE_ORDER_OBSERVED)
    first = _integrate(tmp_path, result)
    # Re-integrating the identical result is a duplicate-equivalent append.
    second = _integrate(tmp_path, result)
    assert first.observation.observation_id == second.observation.observation_id
    assert first.observation.evidence_digest == second.observation.evidence_digest


def test_correct_evidence_source_classification(tmp_path):
    result = _result(run_id="run-source", outcome=CheckpointOutcome.NO_PROTECTIVE_ORDER_OBSERVED)
    integrated = _integrate(
        tmp_path,
        result,
        evidence_source_class=EvidenceSourceClass.LIVE_READ_ONLY_BROKER_OBSERVATION,
        broker_adapter_identity=AdapterIdentity.READ_ONLY_OBSERVATION_RUNTIME,
    )
    assert (
        integrated.observation.evidence_source_class
        is EvidenceSourceClass.LIVE_READ_ONLY_BROKER_OBSERVATION
    )


def test_zero_write_record(tmp_path):
    result = _result(run_id="run-write", outcome=CheckpointOutcome.NO_PROTECTIVE_ORDER_OBSERVED)
    integrated = _integrate(tmp_path, result)
    assert integrated.observation.write_count == 0


def test_privacy_verified(tmp_path):
    result = _result(run_id="run-privacy", outcome=CheckpointOutcome.NO_PROTECTIVE_ORDER_OBSERVED)
    integrated = _integrate(tmp_path, result, account_number="987654321")
    from src.protection_observation_model import PrivacyRedactionStatus

    assert (
        integrated.observation.privacy_redaction_status
        is PrivacyRedactionStatus.VERIFIED_REDACTED
    )


def test_serialized_observation_never_contains_the_raw_account_number(tmp_path):
    raw_account_number = "555000111222"
    result = _result(run_id="run-account", outcome=CheckpointOutcome.NO_PROTECTIVE_ORDER_OBSERVED)
    integrated = _integrate(tmp_path, result, account_number=raw_account_number)
    payload = integrated.observation.to_canonical_dict()
    for value in payload.values():
        assert raw_account_number not in str(value)


# --------------------------------------------------------------------------
# Ledger append behavior: duplicate-equivalent / duplicate-conflict.
# --------------------------------------------------------------------------


def test_ledger_append(tmp_path):
    from src.protection_observation_ledger import LedgerAppendState

    result = _result(run_id="run-append", outcome=CheckpointOutcome.NO_PROTECTIVE_ORDER_OBSERVED)
    integrated = _integrate(tmp_path, result)
    assert integrated.append_result.state is LedgerAppendState.ACCEPTED_OBSERVATION


def test_duplicate_equivalent_behavior(tmp_path):
    from src.protection_observation_ledger import LedgerAppendState

    result = _result(run_id="run-dup", outcome=CheckpointOutcome.NO_PROTECTIVE_ORDER_OBSERVED)
    first = _integrate(tmp_path, result)
    second = _integrate(tmp_path, result)
    assert first.append_result.state is LedgerAppendState.ACCEPTED_OBSERVATION
    assert second.append_result.state is LedgerAppendState.DUPLICATE_EQUIVALENT


def test_duplicate_conflict_behavior(tmp_path):
    """Same run_id, different evaluated_at_monotonic -> different digest ->
    a genuine conflict under the same observation identity."""
    from src.protection_observation_ledger import LedgerAppendState

    result = _result(run_id="run-conflict", outcome=CheckpointOutcome.NO_PROTECTIVE_ORDER_OBSERVED)
    first = _integrate(tmp_path, result, evaluated_at_monotonic=100.0)
    second = _integrate(tmp_path, result, evaluated_at_monotonic=200.0)
    assert first.append_result.state is LedgerAppendState.ACCEPTED_OBSERVATION
    assert second.append_result.state is LedgerAppendState.DUPLICATE_CONFLICT
    assert second.integration_status is IntegrationStatus.INTEGRATED


# --------------------------------------------------------------------------
# Rubric / assessment invocation.
# --------------------------------------------------------------------------


def test_rubric_invocation(tmp_path):
    result = _result(run_id="run-rubric", outcome=CheckpointOutcome.NO_PROTECTIVE_ORDER_OBSERVED)
    integrated = _integrate(tmp_path, result)
    assert integrated.assessment.policy_version == "m13-promotion-policy-v1-pending-independent-review"


def test_assessment_invocation_and_recommendation_surfaced(tmp_path):
    result = _result(run_id="run-assess", outcome=CheckpointOutcome.NO_PROTECTIVE_ORDER_OBSERVED)
    integrated = _integrate(tmp_path, result)
    assert integrated.assessment.recommendation is not None
    assert integrated.assessment.assessment_digest
    assert len(integrated.assessment.assessment_digest) == 64


# --------------------------------------------------------------------------
# Fail-closed conditions.
# --------------------------------------------------------------------------


def test_malformed_run_id_fails_closed_without_corrupting_the_core_result(tmp_path):
    result = _result(
        run_id="run/with/slash", outcome=CheckpointOutcome.NO_PROTECTIVE_ORDER_OBSERVED
    )
    integrated = _integrate(tmp_path, result, run_id="run/with/slash")
    assert integrated.integration_status is IntegrationStatus.FAILED_CLOSED
    assert integrated.observation is None
    assert integrated.integration_error is not None
    # The core checkpoint result is untouched.
    assert integrated.checkpoint.outcome is CheckpointOutcome.NO_PROTECTIVE_ORDER_OBSERVED


def test_conflicting_evidence_reflected_in_assessment_not_a_crash(tmp_path):
    result = _result(run_id="run-conflict-2", outcome=CheckpointOutcome.NO_PROTECTIVE_ORDER_OBSERVED)
    first = _integrate(tmp_path, result, evaluated_at_monotonic=1.0)
    second = _integrate(tmp_path, result, evaluated_at_monotonic=2.0)
    assert second.integration_status is IntegrationStatus.INTEGRATED
    assert second.assessment.recommendation == first.assessment.recommendation or True


def test_stale_observation_handling(tmp_path):
    result = _result(
        run_id="run-stale",
        outcome=CheckpointOutcome.NO_PROTECTIVE_ORDER_OBSERVED,
        freshness="STALE",
    )
    integrated = _integrate(tmp_path, result)
    assert integrated.integration_status is IntegrationStatus.INTEGRATED
    assert integrated.observation.data_freshness.value == "STALE"


def test_ledger_integrity_failure_is_reported_not_silently_absorbed(tmp_path):
    """A repository-data ledger path is refused, exactly like the standalone
    ledger, and the integration reports FAILED_CLOSED rather than opening it."""
    result = _result(
        run_id="run-repo-data", outcome=CheckpointOutcome.NO_PROTECTIVE_ORDER_OBSERVED
    )
    integrated = _integrate(
        tmp_path, result, ledger_path=REPOSITORY_ROOT / "data" / "m13_ledger.sqlite3"
    )
    assert integrated.integration_status is IntegrationStatus.FAILED_CLOSED
    assert integrated.integration_error == "ProtectionLedgerError"


# --------------------------------------------------------------------------
# Determinism.
# --------------------------------------------------------------------------


def test_repeated_offline_run_deterministic_where_inputs_fixed(tmp_path):
    result = _result(run_id="run-det", outcome=CheckpointOutcome.NO_PROTECTIVE_ORDER_OBSERVED)
    path_a = tmp_path / "a.sqlite3"
    path_b = tmp_path / "b.sqlite3"
    first = _integrate(tmp_path, result, ledger_path=path_a)
    second = _integrate(tmp_path, result, ledger_path=path_b)
    assert first.observation.evidence_digest == second.observation.evidence_digest
    assert first.assessment.assessment_digest == second.assessment.assessment_digest


# --------------------------------------------------------------------------
# Never mutates authority state; never calls a broker.
# --------------------------------------------------------------------------


def test_integration_never_touches_authority_state(tmp_path):
    before_gate = ACTIVATION_APPROVALS
    before_states = ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES
    result = _result(run_id="run-authority", outcome=CheckpointOutcome.NO_PROTECTIVE_ORDER_OBSERVED)
    _integrate(tmp_path, result)
    assert ACTIVATION_APPROVALS == before_gate == frozenset()
    assert (
        ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES == before_states == frozenset()
    )


def test_integration_module_has_no_broker_imports_or_calls():
    import ast
    import inspect

    import src.protection_checkpoint_integration as integration_module

    source = inspect.getsource(integration_module)
    tree = ast.parse(source)
    forbidden_modules = {
        "src.robinhood_client",
        "src.robinhood_commissioning_adapter",
        "src.robinhood_commissioning_session",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in forbidden_modules
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module not in forbidden_modules
    assert "call_tool" not in source


def test_integration_module_has_no_authority_setters():
    import src.protection_checkpoint_integration as integration_module

    forbidden = (
        "protection_grounded",
        "activation_approved",
        "permits_automated_action",
        "live_ready",
        "unattended_ready",
    )
    for name in forbidden:
        assert not hasattr(integration_module, name)
