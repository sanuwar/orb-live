"""Adversarial tests for the M14 Issue 2 risk-state evidence provider.

Every fixture is local and offline. Nothing here opens a broker session,
reads credentials, or touches a production database.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.commissioning_risk_state import (
    NON_PASSING_RISK_EVIDENCE_STATUSES,
    RequiredRiskInputAvailability,
    RiskEvidenceStatus,
    RiskStateError,
    RiskStateEvidence,
    evaluate_commissioning_risk_state,
    risk_state_binds_action,
)
from src.risk_limits import (
    AccountRiskState,
    ActionLedger,
    ActionProposal,
    KillSwitchState,
    RiskLimits,
)

RUN_ID = "risk-state-run-1"
ACTION_IDENTITY = "a" * 64


def _availability(**overrides) -> RequiredRiskInputAvailability:
    values = dict(
        kill_switch_source_available=True,
        realized_daily_loss_source_available=True,
        position_exposure_source_available=True,
    )
    values.update(overrides)
    return RequiredRiskInputAvailability(**values)


def _limits(**overrides) -> RiskLimits:
    values = dict(
        maximum_position_quantity=1, maximum_order_value=Decimal("1000"),
        maximum_daily_loss=Decimal("500"), maximum_open_positions=1,
    )
    values.update(overrides)
    return RiskLimits(**values)


def _state(**overrides) -> AccountRiskState:
    values = dict(realized_daily_loss=Decimal("0"), open_position_count=0)
    values.update(overrides)
    return AccountRiskState(**values)


def _proposal(**overrides) -> ActionProposal:
    values = dict(
        idempotency_key=f"{RUN_ID}:entry", quantity=1,
        order_value=Decimal("100"), observed_monotonic=1.0,
    )
    values.update(overrides)
    return ActionProposal(**values)


def _full_inputs(**overrides):
    kwargs = dict(
        run_id=RUN_ID, action_identity=ACTION_IDENTITY, availability=_availability(),
        proposal=_proposal(), limits=_limits(), state=_state(),
        kill_switch=KillSwitchState.DISENGAGED, ledger=ActionLedger(),
        current_monotonic=1.0,
    )
    kwargs.update(overrides)
    return kwargs


# ---------------------------------------------------------------------
# STATUS ENUM SHAPE
# ---------------------------------------------------------------------


def test_exactly_one_status_is_passing():
    assert RiskEvidenceStatus.AVAILABLE_VALID not in NON_PASSING_RISK_EVIDENCE_STATUSES
    for status in RiskEvidenceStatus:
        if status is not RiskEvidenceStatus.AVAILABLE_VALID:
            assert status in NON_PASSING_RISK_EVIDENCE_STATUSES


def test_six_distinct_statuses_exist():
    assert {member.value for member in RiskEvidenceStatus} == {
        "AVAILABLE_VALID", "AVAILABLE_BLOCKING", "UNAVAILABLE",
        "STALE", "INCONSISTENT", "ERROR",
    }


# ---------------------------------------------------------------------
# REQUIRED-INPUT AVAILABILITY HAS NO DEFAULT
# ---------------------------------------------------------------------


def test_availability_requires_every_field():
    with pytest.raises(TypeError):
        RequiredRiskInputAvailability(kill_switch_source_available=True)  # type: ignore[call-arg]


def test_availability_rejects_non_bool_values():
    with pytest.raises(RiskStateError):
        RequiredRiskInputAvailability(
            kill_switch_source_available=1,  # type: ignore[arg-type]
            realized_daily_loss_source_available=True,
            position_exposure_source_available=True,
        )


# ---------------------------------------------------------------------
# EXPLICIT PASS
# ---------------------------------------------------------------------


def test_explicit_risk_pass_allows():
    evidence = evaluate_commissioning_risk_state(**_full_inputs())
    assert evidence.status is RiskEvidenceStatus.AVAILABLE_VALID
    assert evidence.passes is True
    assert evidence.reason_codes == ()


# ---------------------------------------------------------------------
# LIMIT VIOLATION DENIES
# ---------------------------------------------------------------------


def test_limit_violation_denies():
    evidence = evaluate_commissioning_risk_state(
        **_full_inputs(proposal=_proposal(quantity=2))
    )
    assert evidence.status is RiskEvidenceStatus.AVAILABLE_BLOCKING
    assert evidence.status in NON_PASSING_RISK_EVIDENCE_STATUSES
    assert any("DENIED_POSITION_SIZE" in code for code in evidence.reason_codes)


def test_kill_switch_engaged_denies():
    evidence = evaluate_commissioning_risk_state(
        **_full_inputs(kill_switch=KillSwitchState.ENGAGED)
    )
    assert evidence.status is RiskEvidenceStatus.AVAILABLE_BLOCKING
    assert any("KILL_SWITCH_ENGAGED" in code for code in evidence.reason_codes)


def test_daily_loss_limit_denies():
    evidence = evaluate_commissioning_risk_state(
        **_full_inputs(state=_state(realized_daily_loss=Decimal("500")))
    )
    assert evidence.status is RiskEvidenceStatus.AVAILABLE_BLOCKING
    assert any("DAILY_LOSS" in code for code in evidence.reason_codes)


def test_duplicate_action_denies():
    ledger = ActionLedger()
    ledger.record(f"{RUN_ID}:entry", approved_quantity=1)
    evidence = evaluate_commissioning_risk_state(**_full_inputs(ledger=ledger))
    assert evidence.status is RiskEvidenceStatus.AVAILABLE_BLOCKING
    assert any("DUPLICATE_ACTION" in code for code in evidence.reason_codes)


# ---------------------------------------------------------------------
# UNAVAILABLE REQUIRED RISK STATE DENIES
# ---------------------------------------------------------------------


def test_kill_switch_source_unavailable_denies():
    evidence = evaluate_commissioning_risk_state(
        **_full_inputs(availability=_availability(kill_switch_source_available=False))
    )
    assert evidence.status is RiskEvidenceStatus.UNAVAILABLE
    assert "RISK_STATE_KILL_SWITCH_SOURCE_UNAVAILABLE" in evidence.reason_codes


def test_realized_daily_loss_source_unavailable_denies():
    evidence = evaluate_commissioning_risk_state(
        **_full_inputs(
            availability=_availability(realized_daily_loss_source_available=False)
        )
    )
    assert evidence.status is RiskEvidenceStatus.UNAVAILABLE
    assert "RISK_STATE_REALIZED_DAILY_LOSS_SOURCE_UNAVAILABLE" in evidence.reason_codes


def test_position_exposure_source_unavailable_denies():
    evidence = evaluate_commissioning_risk_state(
        **_full_inputs(
            availability=_availability(position_exposure_source_available=False)
        )
    )
    assert evidence.status is RiskEvidenceStatus.UNAVAILABLE
    assert "RISK_STATE_POSITION_EXPOSURE_SOURCE_UNAVAILABLE" in evidence.reason_codes


def test_all_sources_unavailable_reports_every_missing_reason():
    evidence = evaluate_commissioning_risk_state(
        **_full_inputs(availability=_availability(
            kill_switch_source_available=False,
            realized_daily_loss_source_available=False,
            position_exposure_source_available=False,
        ))
    )
    assert evidence.status is RiskEvidenceStatus.UNAVAILABLE
    assert len(evidence.reason_codes) == 3


def test_unavailable_declaration_wins_even_with_valid_inputs_supplied():
    """An honest UNAVAILABLE declaration must not be overridden by a caller
    that also happens to supply well-formed, WITHIN_LIMITS-passing objects.
    Availability is checked before any value is inspected."""
    evidence = evaluate_commissioning_risk_state(
        **_full_inputs(
            availability=_availability(kill_switch_source_available=False),
            proposal=_proposal(), limits=_limits(), state=_state(),
            kill_switch=KillSwitchState.DISENGAGED,
        )
    )
    assert evidence.status is RiskEvidenceStatus.UNAVAILABLE


def test_missing_availability_declaration_denies():
    evidence = evaluate_commissioning_risk_state(
        **_full_inputs(availability=None)  # type: ignore[arg-type]
    )
    assert evidence.status is RiskEvidenceStatus.UNAVAILABLE


# ---------------------------------------------------------------------
# STALE RISK STATE DENIES
# ---------------------------------------------------------------------


def test_stale_risk_state_denies():
    evidence = evaluate_commissioning_risk_state(
        **_full_inputs(proposal=_proposal(observed_monotonic=1.0), current_monotonic=100.0)
    )
    assert evidence.status is RiskEvidenceStatus.STALE
    assert any("DENIED_STALE_DATA" in code for code in evidence.reason_codes)


def test_future_observation_is_stale_not_valid():
    evidence = evaluate_commissioning_risk_state(
        **_full_inputs(proposal=_proposal(observed_monotonic=100.0), current_monotonic=1.0)
    )
    assert evidence.status is RiskEvidenceStatus.STALE


# ---------------------------------------------------------------------
# INCONSISTENT: DECLARED AVAILABLE BUT NOT SUPPLIED
# ---------------------------------------------------------------------


def test_declared_available_but_missing_state_is_inconsistent():
    evidence = evaluate_commissioning_risk_state(**_full_inputs(state=None))
    assert evidence.status is RiskEvidenceStatus.INCONSISTENT


def test_declared_available_but_missing_kill_switch_is_inconsistent():
    evidence = evaluate_commissioning_risk_state(**_full_inputs(kill_switch=None))
    assert evidence.status is RiskEvidenceStatus.INCONSISTENT


# ---------------------------------------------------------------------
# EXCEPTION DURING EVALUATION DENIES, NEVER PROPAGATES, NEVER DEFAULTS TO PASS
# ---------------------------------------------------------------------


class _ExplodingLedger:
    """Structurally satisfies nothing; every call raises."""

    def has_seen(self, key: str) -> bool:
        raise RuntimeError("ledger backing store unavailable")

    def record(self, key: str, approved_quantity: int) -> None:
        raise RuntimeError("boom")

    @property
    def approved_quantity(self) -> int:
        raise RuntimeError("boom")


def test_exception_during_evaluation_denies_and_never_propagates():
    evidence = evaluate_commissioning_risk_state(**_full_inputs(ledger=_ExplodingLedger()))
    assert evidence.status is RiskEvidenceStatus.ERROR
    assert evidence.status in NON_PASSING_RISK_EVIDENCE_STATUSES


def test_malformed_run_id_never_raises():
    evidence = evaluate_commissioning_risk_state(**_full_inputs(run_id=""))
    assert evidence.status is RiskEvidenceStatus.ERROR


def test_malformed_action_identity_never_raises():
    evidence = evaluate_commissioning_risk_state(**_full_inputs(action_identity=123))  # type: ignore[arg-type]
    assert evidence.status is RiskEvidenceStatus.ERROR


def test_provider_never_raises_for_any_garbage_input():
    """A broad sweep: nothing thrown here should ever escape as an exception."""
    garbage_kwargs = [
        dict(run_id=None, action_identity=None, availability=None),
        dict(run_id=RUN_ID, action_identity=ACTION_IDENTITY, availability="not-availability"),
        _full_inputs(proposal="not-a-proposal"),
        _full_inputs(limits="not-limits"),
        _full_inputs(current_monotonic="not-a-number"),
    ]
    for kwargs in garbage_kwargs:
        evidence = evaluate_commissioning_risk_state(**kwargs)
        assert isinstance(evidence, RiskStateEvidence)
        assert evidence.status is not RiskEvidenceStatus.AVAILABLE_VALID


# ---------------------------------------------------------------------
# IDENTITY BINDING: EVIDENCE IS BOUND TO THE EXACT RUN/ACTION
# ---------------------------------------------------------------------


def test_evidence_binds_to_its_own_run_and_action():
    evidence = evaluate_commissioning_risk_state(**_full_inputs())
    assert risk_state_binds_action(
        evidence, run_id=RUN_ID, action_identity=ACTION_IDENTITY
    )


def test_evidence_does_not_bind_a_different_run_id():
    evidence = evaluate_commissioning_risk_state(**_full_inputs())
    assert not risk_state_binds_action(
        evidence, run_id="a-different-run", action_identity=ACTION_IDENTITY
    )


def test_evidence_does_not_bind_a_different_action_identity():
    evidence = evaluate_commissioning_risk_state(**_full_inputs())
    assert not risk_state_binds_action(
        evidence, run_id=RUN_ID, action_identity="b" * 64
    )


def test_binds_action_rejects_non_evidence_objects():
    assert not risk_state_binds_action(
        "not-evidence", run_id=RUN_ID, action_identity=ACTION_IDENTITY
    )
    assert not risk_state_binds_action(
        None, run_id=RUN_ID, action_identity=ACTION_IDENTITY
    )


# ---------------------------------------------------------------------
# CRYPTOGRAPHIC / STRUCTURAL BINDING OF THE DIGEST ITSELF
# ---------------------------------------------------------------------


def test_digest_deterministic_for_identical_inputs():
    first = evaluate_commissioning_risk_state(**_full_inputs())
    second = evaluate_commissioning_risk_state(**_full_inputs())
    assert first.evidence_digest == second.evidence_digest
    assert first == second


def test_digest_changes_when_run_id_changes():
    first = evaluate_commissioning_risk_state(**_full_inputs())
    second = evaluate_commissioning_risk_state(
        **_full_inputs(run_id="different-run", proposal=_proposal(
            idempotency_key="different-run:entry",
        ))
    )
    assert first.evidence_digest != second.evidence_digest


def test_digest_changes_when_action_identity_changes():
    first = evaluate_commissioning_risk_state(**_full_inputs())
    second = evaluate_commissioning_risk_state(
        **_full_inputs(action_identity="c" * 64)
    )
    assert first.evidence_digest != second.evidence_digest


def test_digest_changes_when_status_changes():
    passing = evaluate_commissioning_risk_state(**_full_inputs())
    blocking = evaluate_commissioning_risk_state(
        **_full_inputs(proposal=_proposal(quantity=2))
    )
    assert passing.evidence_digest != blocking.evidence_digest


def test_digest_changes_when_reason_codes_differ():
    kill_switch_missing = evaluate_commissioning_risk_state(
        **_full_inputs(availability=_availability(kill_switch_source_available=False))
    )
    loss_missing = evaluate_commissioning_risk_state(
        **_full_inputs(
            availability=_availability(realized_daily_loss_source_available=False)
        )
    )
    assert kill_switch_missing.status is RiskEvidenceStatus.UNAVAILABLE
    assert loss_missing.status is RiskEvidenceStatus.UNAVAILABLE
    assert kill_switch_missing.evidence_digest != loss_missing.evidence_digest


# ---------------------------------------------------------------------
# NO PASSING STATUS IS EVER PRODUCED BY A DENY-PRODUCING PATH
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs_factory",
    [
        lambda: _full_inputs(availability=_availability(kill_switch_source_available=False)),
        lambda: _full_inputs(proposal=_proposal(quantity=2)),
        lambda: _full_inputs(current_monotonic=999.0),
        lambda: _full_inputs(state=None),
        lambda: _full_inputs(ledger=_ExplodingLedger()),
        lambda: _full_inputs(run_id=""),
    ],
)
def test_every_denying_path_never_reports_available_valid(kwargs_factory):
    evidence = evaluate_commissioning_risk_state(**kwargs_factory())
    assert evidence.status is not RiskEvidenceStatus.AVAILABLE_VALID
    assert evidence.status in NON_PASSING_RISK_EVIDENCE_STATUSES
