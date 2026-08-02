"""Supervised-execution gate — defined, inert, and provably unopenable.

Every test is local and offline.

The property that matters most: **no combination of inputs opens this gate.**
Not "no combination we thought to try" — the test enumerates all 512
precondition combinations and asserts every one stays closed.
"""

from __future__ import annotations

import ast
import dataclasses
import itertools
import pathlib

import pytest

import src.execution_gates as gates
from src.execution_gates import (
    ACTIVATION_APPROVALS,
    AUTHORITY_EXPLANATION,
    REQUIRED_PRECONDITIONS,
    ExecutionGateError,
    GateDecision,
    GateMode,
    GatePreconditions,
    GateVerdict,
    gate_readiness_report,
    open_gate,
)

MODULE_PATH = pathlib.Path(gates.__file__)

PINNED_MODULES = (
    "read_only_observation_runtime",
    "read_only_broker_state",
    "read_only_authorization_manifest",
    "live_order_state_model",
)


def preconditions(**overrides) -> GatePreconditions:
    values = {name: True for name in REQUIRED_PRECONDITIONS}
    values.update(overrides)
    return GatePreconditions(**values)


# --------------------------------------------------------------------------
# 1. The gate cannot open.
# --------------------------------------------------------------------------


def test_no_mode_is_approved_for_activation():
    assert ACTIVATION_APPROVALS == frozenset()


def test_every_precondition_satisfied_still_does_not_open():
    decision = open_gate(preconditions())
    assert decision.verdict is GateVerdict.CLOSED_ACTIVATION_NOT_APPROVED
    assert decision.is_open is False


def test_no_combination_of_preconditions_opens_the_gate():
    """All 2^9 combinations, not a sample."""
    checked = 0
    for combination in itertools.product([True, False], repeat=len(REQUIRED_PRECONDITIONS)):
        values = dict(zip(REQUIRED_PRECONDITIONS, combination))
        decision = open_gate(GatePreconditions(**values))
        assert decision.is_open is False
        assert decision.permits_automated_action is False
        checked += 1
    assert checked == 512


def test_an_open_decision_cannot_be_constructed():
    """Structural, not a matter of the function happening not to return one."""
    with pytest.raises(ExecutionGateError):
        GateDecision(GateVerdict.OPEN, GateMode.SUPERVISED)


def test_activation_is_checked_before_preconditions():
    """A caller who met everything must be told the gate is not approved.

    Reporting a missing precondition instead invites someone to go fix the
    condition and try again, which is the wrong lesson entirely.
    """
    decision = open_gate(preconditions())
    assert decision.verdict is GateVerdict.CLOSED_ACTIVATION_NOT_APPROVED
    decision = open_gate(preconditions(risk_within_limits=False))
    assert decision.verdict is GateVerdict.CLOSED_ACTIVATION_NOT_APPROVED


def test_the_readiness_report_says_the_gate_is_not_openable():
    report = gate_readiness_report()
    assert report["activation_approved"] is False
    assert report["gate_is_openable"] is False
    assert report["approved_modes"] == []
    assert report["required_precondition_count"] == 9


# --------------------------------------------------------------------------
# 2. Preconditions.
# --------------------------------------------------------------------------


def test_the_required_precondition_list_matches_the_dataclass():
    names = tuple(f.name for f in dataclasses.fields(GatePreconditions))
    assert names == REQUIRED_PRECONDITIONS
    assert len(REQUIRED_PRECONDITIONS) == 9


def test_there_are_no_default_preconditions():
    """A default would mean an unstated precondition counts as satisfied."""
    for field in dataclasses.fields(GatePreconditions):
        assert field.default is dataclasses.MISSING, field.name
        assert field.default_factory is dataclasses.MISSING, field.name
    with pytest.raises(TypeError):
        GatePreconditions()


@pytest.mark.parametrize("name", REQUIRED_PRECONDITIONS)
def test_each_precondition_alone_can_close_the_gate(name):
    unmet = preconditions(**{name: False}).unmet()
    assert name in unmet
    assert len(unmet) == 1


def test_all_met_requires_every_one():
    assert preconditions().all_met is True
    for name in REQUIRED_PRECONDITIONS:
        assert preconditions(**{name: False}).all_met is False


@pytest.mark.parametrize("value", [1, "true", "yes", [], None, 0])
def test_a_truthy_non_bool_precondition_is_refused(value):
    """A truthy non-bool is how "unknown" sneaks in as "yes"."""
    with pytest.raises(ExecutionGateError):
        preconditions(risk_within_limits=value)


def test_the_precondition_set_covers_the_expected_ground():
    """Named individually so removing one is a visible diff, not a silent gap."""
    assert set(REQUIRED_PRECONDITIONS) == {
        "operator_present_and_acknowledged",
        "strategy_enabled",
        "protection_grounding_complete",
        "broker_state_verified_fresh",
        "recovery_consistent",
        "risk_within_limits",
        "kill_switch_disengaged",
        "monitoring_healthy",
        "market_session_valid",
    }


def test_protection_grounding_is_a_precondition():
    """The held Milestone 9 checkpoint is on this gate's critical path."""
    assert "protection_grounding_complete" in REQUIRED_PRECONDITIONS
    assert preconditions(protection_grounding_complete=False).all_met is False


def test_strategy_enabled_is_a_precondition_not_an_assumption():
    assert "strategy_enabled" in REQUIRED_PRECONDITIONS
    assert preconditions(strategy_enabled=False).all_met is False


# --------------------------------------------------------------------------
# 3. Decisions.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("verdict", [v for v in GateVerdict if v is not GateVerdict.OPEN])
def test_no_closed_verdict_permits_action(verdict):
    decision = GateDecision(verdict, GateMode.SUPERVISED)
    assert decision.permits_automated_action is False
    assert decision.is_open is False


def test_a_permitting_decision_cannot_be_constructed():
    with pytest.raises(ExecutionGateError):
        GateDecision(
            GateVerdict.CLOSED_PRECONDITION_UNMET, GateMode.SUPERVISED,
            permits_automated_action=True,
        )


def test_a_malformed_precondition_record_closes_rather_than_raises():
    decision = open_gate("everything is fine")
    assert decision.verdict is GateVerdict.CLOSED_MALFORMED_INPUT


def test_an_invalid_mode_is_refused():
    with pytest.raises(ExecutionGateError):
        open_gate(preconditions(), mode="SUPERVISED")


def test_the_explanation_names_what_is_missing():
    assert "ACTIVATION_APPROVALS" in AUTHORITY_EXPLANATION
    assert "empty" in AUTHORITY_EXPLANATION


def test_the_mode_vocabulary_is_closed():
    """Two modes, and neither is approved.

    This asserted `SUPERVISED` alone until the limited-unattended mode landed.
    Pinning the whole set keeps a third mode from arriving unnoticed.
    """
    assert {mode.value for mode in GateMode} == {"SUPERVISED", "LIMITED_UNATTENDED"}
    assert ACTIVATION_APPROVALS == frozenset()


# --------------------------------------------------------------------------
# 4. Privacy.
# --------------------------------------------------------------------------

ALLOWED_FACT_VALUES = {
    "gate_verdict": {verdict.value for verdict in GateVerdict},
    "gate_mode": {mode.value for mode in GateMode},
    "unmet_precondition_bucket": {"none", "one", "many"},
    "permits_automated_action": {"false"},
}


def test_gate_facts_are_allowlisted():
    for verdict in GateVerdict:
        if verdict is GateVerdict.OPEN:
            continue
        for count in (0, 1, 5):
            facts = GateDecision(verdict, GateMode.SUPERVISED, count).sanitized_facts()
            assert set(facts) == set(ALLOWED_FACT_VALUES)
            for key, value in facts.items():
                assert value in ALLOWED_FACT_VALUES[key], (key, value)


def test_unmet_preconditions_are_counted_not_named():
    """Which precondition failed can describe account state; how many cannot."""
    decision = open_gate(
        preconditions(risk_within_limits=False, kill_switch_disengaged=False)
    )
    facts = decision.sanitized_facts()
    assert facts["unmet_precondition_bucket"] == "many"
    for name in REQUIRED_PRECONDITIONS:
        assert name not in repr(facts)


def test_the_decision_has_no_field_that_could_carry_broker_data():
    names = {f.name for f in dataclasses.fields(GateDecision)}
    for forbidden in ("symbol", "price", "quantity", "account", "identifier", "payload"):
        assert not any(forbidden in name for name in names), forbidden


def test_facts_survive_the_commissioning_privacy_sanitizer():
    from src.one_share_commissioning import validate_sanitized_evidence

    validate_sanitized_evidence(
        open_gate(preconditions()).sanitized_facts()
    )


# --------------------------------------------------------------------------
# 5. Independence and inertness.
# --------------------------------------------------------------------------


def _imports(path: pathlib.Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_the_gate_imports_no_pinned_checkpoint_file():
    imported = _imports(MODULE_PATH)
    for pinned in PINNED_MODULES:
        assert not any(pinned in name for name in imported), pinned


def test_the_gate_imports_nothing_at_all_from_this_repository():
    """It takes decided facts rather than re-deriving them.

    A gate that re-derives its own inputs can disagree with the modules that
    produced them, and the disagreement would be invisible.
    """
    imported = _imports(MODULE_PATH)
    assert not any(name.startswith("src") for name in imported), imported


def test_the_gate_performs_no_io_and_no_network():
    imported = _imports(MODULE_PATH)
    assert imported.isdisjoint(
        {"socket", "http", "urllib", "requests", "os", "pathlib", "subprocess"}
    )
    for node in ast.walk(ast.parse(MODULE_PATH.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.attr if isinstance(func, ast.Attribute)
                else func.id if isinstance(func, ast.Name)
                else None
            )
            assert name not in {"open", "urlopen", "write_text", "print"}


def test_the_gate_defines_no_write_shaped_function():
    forbidden = {"place", "cancel", "submit", "activate", "execute", "send"}
    for node in ast.walk(ast.parse(MODULE_PATH.read_text(encoding="utf-8"))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            tokens = set(node.name.lower().strip("_").split("_"))
            assert not (tokens & forbidden), node.name


def test_activation_approvals_is_not_populated_anywhere_in_the_module():
    """Populating it must be an operator configuration change, never code."""
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "ACTIVATION_APPROVALS: frozenset[GateMode] = frozenset()" in source
    assert "ACTIVATION_APPROVALS.add" not in source
    assert "ACTIVATION_APPROVALS |" not in source


def test_the_repository_still_has_strategy_disabled():
    """The gate names strategy_enabled as a precondition; it is still false."""
    from src.config import load_config

    assert load_config()["strategy"]["enabled"] is False


# --------------------------------------------------------------------------
# 6. Limited unattended operation (task H).
# --------------------------------------------------------------------------

from src.execution_gates import (  # noqa: E402
    UNATTENDED_PRECONDITIONS,
    UnattendedPreconditions,
    open_unattended_gate,
)


def unattended(**overrides) -> UnattendedPreconditions:
    values = {name: True for name in UNATTENDED_PRECONDITIONS}
    values.update(overrides)
    return UnattendedPreconditions(**values)


def test_unattended_mode_exists_and_is_not_approved():
    assert GateMode.LIMITED_UNATTENDED not in ACTIVATION_APPROVALS


def test_the_unattended_gate_cannot_open_with_everything_satisfied():
    decision = open_unattended_gate(preconditions(), unattended())
    assert decision.verdict is GateVerdict.CLOSED_ACTIVATION_NOT_APPROVED
    assert decision.is_open is False
    assert decision.permits_automated_action is False


def test_unattended_requires_strictly_more_than_supervised():
    """The absence of a human is the reason to require more, not less."""
    assert set(UNATTENDED_PRECONDITIONS).isdisjoint(REQUIRED_PRECONDITIONS)
    total = len(REQUIRED_PRECONDITIONS) + len(UNATTENDED_PRECONDITIONS)
    assert total == 17
    assert total > len(REQUIRED_PRECONDITIONS)


def test_unattended_still_requires_every_supervised_precondition():
    """No condition is relaxed because nobody is watching."""
    for name in REQUIRED_PRECONDITIONS:
        decision = open_unattended_gate(
            preconditions(**{name: False}), unattended()
        )
        assert decision.is_open is False
        assert decision.unmet_precondition_count >= 1


def test_operator_acknowledgement_is_still_required_unattended():
    """Unattended means no human watches the session, not that none authorized
    the deployment. The acknowledgement moves earlier; it does not vanish."""
    assert "operator_present_and_acknowledged" in REQUIRED_PRECONDITIONS
    decision = open_unattended_gate(
        preconditions(operator_present_and_acknowledged=False), unattended()
    )
    assert decision.unmet_precondition_count == 1


@pytest.mark.parametrize("name", UNATTENDED_PRECONDITIONS)
def test_each_unattended_precondition_alone_registers_as_unmet(name):
    decision = open_unattended_gate(preconditions(), unattended(**{name: False}))
    assert decision.unmet_precondition_count == 1
    assert decision.is_open is False


def test_the_unmet_count_spans_both_records():
    """An operator reading "two unmet" should not have to ask which list."""
    decision = open_unattended_gate(
        preconditions(risk_within_limits=False),
        unattended(alerting_verified_reachable=False),
    )
    assert decision.unmet_precondition_count == 2


def test_the_unattended_precondition_set_covers_the_expected_ground():
    assert set(UNATTENDED_PRECONDITIONS) == {
        "capital_tier_approved",
        "deployment_not_expired",
        "within_permitted_trading_window",
        "alerting_verified_reachable",
        "kill_switch_remotely_operable",
        "automatic_suspension_armed",
        "no_unresolved_operator_action",
        "supervised_evidence_reviewed",
    }


def test_a_remotely_operable_kill_switch_is_required():
    """A kill switch needing physical access is not a kill switch at 3am."""
    assert "kill_switch_remotely_operable" in UNATTENDED_PRECONDITIONS


def test_alerting_must_be_verified_before_the_session_not_during():
    assert "alerting_verified_reachable" in UNATTENDED_PRECONDITIONS


def test_supervised_evidence_must_be_reviewed_first():
    """Unattended follows supervised; it does not skip it."""
    assert "supervised_evidence_reviewed" in UNATTENDED_PRECONDITIONS


def test_no_combination_of_unattended_preconditions_opens_the_gate():
    """All 256 unattended combinations, with supervised fully satisfied."""
    checked = 0
    for combination in itertools.product(
        [True, False], repeat=len(UNATTENDED_PRECONDITIONS)
    ):
        values = dict(zip(UNATTENDED_PRECONDITIONS, combination))
        decision = open_unattended_gate(preconditions(), UnattendedPreconditions(**values))
        assert decision.is_open is False
        assert decision.permits_automated_action is False
        checked += 1
    assert checked == 256


def test_there_are_no_default_unattended_preconditions():
    for field in dataclasses.fields(UnattendedPreconditions):
        assert field.default is dataclasses.MISSING, field.name
    with pytest.raises(TypeError):
        UnattendedPreconditions()


@pytest.mark.parametrize("value", [1, "true", None, []])
def test_a_truthy_non_bool_unattended_precondition_is_refused(value):
    with pytest.raises(ExecutionGateError):
        unattended(capital_tier_approved=value)


def test_malformed_unattended_input_closes_rather_than_raises():
    assert open_unattended_gate(preconditions(), "fine").verdict is (
        GateVerdict.CLOSED_MALFORMED_INPUT
    )
    assert open_unattended_gate("fine", unattended()).verdict is (
        GateVerdict.CLOSED_MALFORMED_INPUT
    )


def test_an_open_unattended_decision_cannot_be_constructed():
    with pytest.raises(ExecutionGateError):
        GateDecision(GateVerdict.OPEN, GateMode.LIMITED_UNATTENDED)


def test_the_readiness_report_counts_both_requirement_sets():
    report = gate_readiness_report()
    assert report["required_precondition_count"] == 9
    assert report["unattended_precondition_count"] == 8
    assert report["total_unattended_requirement_count"] == 17
    assert report["gate_is_openable"] is False


def test_unattended_facts_are_allowlisted():
    decision = open_unattended_gate(preconditions(), unattended())
    facts = decision.sanitized_facts()
    assert set(facts) == set(ALLOWED_FACT_VALUES)
    for key, value in facts.items():
        assert value in ALLOWED_FACT_VALUES[key], (key, value)
    assert facts["gate_mode"] == "LIMITED_UNATTENDED"
