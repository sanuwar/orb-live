"""Supervised-execution gate — defined, inert, and provably unopenable.

Every test is local and offline.

Two properties matter most:

1. **No combination of inputs opens this gate.** Not "no combination we
   thought to try" — the tests enumerate all 512 precondition combinations.
2. **Caller-supplied booleans can never satisfy a precondition.** Evidence has
   a source and an age, and `SELF_DECLARED` is accepted nowhere.

Where a test checks an evidence *rule*, it uses an independently written
expectation rather than reading `REQUIRED_EVIDENCE` — otherwise it would be
validating the mapping against itself.
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
    MAXIMUM_ATTESTATION_AGE_SECONDS,
    REQUIRED_EVIDENCE,
    REQUIRED_PRECONDITIONS,
    Attestation,
    EvidenceSource,
    ExecutionGateError,
    GateDecision,
    GateMode,
    GatePreconditions,
    GateVerdict,
    evidence_is_acceptable,
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

NOW = 1000.0
FRESH = NOW - 10.0

#: Independently written expectation of what each precondition will accept.
#: Deliberately NOT derived from `REQUIRED_EVIDENCE` — a test that reads the
#: mapping it is validating proves only that the mapping equals itself.
EXPECTED_EVIDENCE = {
    "operator_present_and_acknowledged": {"OPERATOR_ATTESTED"},
    "strategy_enabled": {"OPERATOR_ATTESTED"},
    "protection_grounding_complete": {"OPERATOR_ATTESTED"},
    "broker_state_verified_fresh": {"MEASURED"},
    "recovery_consistent": {"MEASURED"},
    "risk_within_limits": {"MEASURED"},
    "kill_switch_disengaged": {"MEASURED"},
    "monitoring_healthy": {"MEASURED", "TESTED"},
    "market_session_valid": {"MEASURED"},
}


def good_source(name: str) -> EvidenceSource:
    """An acceptable source for `name`, chosen from the independent table."""
    return EvidenceSource(sorted(EXPECTED_EVIDENCE[name])[0])


def att(name: str, *, satisfied: bool = True, source=None, at: float = FRESH):
    return Attestation(
        satisfied=satisfied,
        source=source if source is not None else good_source(name),
        recorded_monotonic=at,
    )


def preconditions(**overrides) -> GatePreconditions:
    values = {name: att(name) for name in REQUIRED_PRECONDITIONS}
    values.update(overrides)
    return GatePreconditions(**values)


def gate(pre=None, **kwargs):
    return open_gate(
        pre if pre is not None else preconditions(),
        current_monotonic=kwargs.pop("current_monotonic", NOW),
        **kwargs,
    )


# --------------------------------------------------------------------------
# 1. The gate cannot open.
# --------------------------------------------------------------------------


def test_no_mode_is_approved_for_activation():
    assert ACTIVATION_APPROVALS == frozenset()


def test_perfect_evidence_still_does_not_open_the_gate():
    decision = gate()
    assert decision.verdict is GateVerdict.CLOSED_ACTIVATION_NOT_APPROVED
    assert decision.is_open is False
    assert decision.unmet_precondition_count == 0


def test_no_combination_of_preconditions_opens_the_gate():
    """All 2^9 satisfied/unsatisfied combinations, with correct sources."""
    checked = 0
    for combination in itertools.product([True, False], repeat=len(REQUIRED_PRECONDITIONS)):
        values = {
            name: att(name, satisfied=flag)
            for name, flag in zip(REQUIRED_PRECONDITIONS, combination)
        }
        decision = gate(GatePreconditions(**values))
        assert decision.is_open is False
        assert decision.permits_automated_action is False
        checked += 1
    assert checked == 512


def test_no_combination_of_evidence_sources_opens_the_gate():
    """Every source for every field, satisfied and fresh. Still closed."""
    for name in REQUIRED_PRECONDITIONS:
        for source in EvidenceSource:
            decision = gate(preconditions(**{name: att(name, source=source)}))
            assert decision.is_open is False, (name, source)


def test_an_open_decision_cannot_be_constructed():
    """Structural, not a matter of the function happening not to return one."""
    with pytest.raises(ExecutionGateError):
        GateDecision(GateVerdict.OPEN, GateMode.SUPERVISED)


def test_activation_is_checked_before_preconditions():
    """A caller who met everything must be told the gate is not approved."""
    assert gate().verdict is GateVerdict.CLOSED_ACTIVATION_NOT_APPROVED
    assert gate(
        preconditions(risk_within_limits=att("risk_within_limits", satisfied=False))
    ).verdict is GateVerdict.CLOSED_ACTIVATION_NOT_APPROVED


def test_the_readiness_report_says_the_gate_is_not_openable():
    report = gate_readiness_report()
    assert report["activation_approved"] is False
    assert report["gate_is_openable"] is False
    assert report["approved_modes"] == []
    assert report["required_precondition_count"] == 9
    assert report["self_declared_evidence_accepted_anywhere"] is False


# --------------------------------------------------------------------------
# 2. Raw booleans are rejected.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", REQUIRED_PRECONDITIONS)
def test_a_raw_boolean_is_rejected_for_every_precondition(name):
    """The interruption-era design took these. It no longer does."""
    with pytest.raises(ExecutionGateError):
        preconditions(**{name: True})
    with pytest.raises(ExecutionGateError):
        preconditions(**{name: False})


def test_a_fully_boolean_record_cannot_be_constructed():
    with pytest.raises(ExecutionGateError):
        GatePreconditions(**{name: True for name in REQUIRED_PRECONDITIONS})


@pytest.mark.parametrize("value", [1, 0, "true", "yes", [], {}, None, 1.0, object()])
def test_a_non_attestation_precondition_is_rejected(value):
    with pytest.raises(ExecutionGateError):
        preconditions(strategy_enabled=value)


@pytest.mark.parametrize("value", [1, 0, "true", "yes", [], None, 1.0])
def test_a_truthy_non_bool_satisfied_flag_is_rejected(value):
    """A truthy non-bool is how "unknown" sneaks in as "yes"."""
    with pytest.raises(ExecutionGateError):
        Attestation(satisfied=value, source=EvidenceSource.MEASURED,
                    recorded_monotonic=FRESH)


# --------------------------------------------------------------------------
# 3. SELF_DECLARED is never sufficient.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", REQUIRED_PRECONDITIONS)
def test_self_declared_is_rejected_for_every_precondition(name):
    record = preconditions(**{name: att(name, source=EvidenceSource.SELF_DECLARED)})
    assert name in record.unmet(current_monotonic=NOW)
    assert evidence_is_acceptable(name, att(name, source=EvidenceSource.SELF_DECLARED)) is False


def test_an_entirely_self_declared_record_satisfies_nothing():
    record = GatePreconditions(**{
        name: att(name, source=EvidenceSource.SELF_DECLARED)
        for name in REQUIRED_PRECONDITIONS
    })
    assert len(record.unmet(current_monotonic=NOW)) == len(REQUIRED_PRECONDITIONS)
    assert record.all_met(current_monotonic=NOW) is False


def test_self_declared_appears_in_no_accepted_set():
    for name in REQUIRED_PRECONDITIONS:
        assert EvidenceSource.SELF_DECLARED not in REQUIRED_EVIDENCE[name], name


# --------------------------------------------------------------------------
# 4. Per-field evidence rules.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", REQUIRED_PRECONDITIONS)
def test_each_precondition_accepts_exactly_its_expected_sources(name):
    """Checked against the independently written table, not the module's."""
    expected = {EvidenceSource(value) for value in EXPECTED_EVIDENCE[name]}
    actual = {
        source for source in EvidenceSource
        if evidence_is_acceptable(name, att(name, source=source))
    }
    assert actual == expected


def test_the_independent_table_covers_every_precondition():
    assert set(EXPECTED_EVIDENCE) == set(REQUIRED_PRECONDITIONS)


def test_decisions_require_a_human_and_observations_require_a_measurement():
    """The distinction is deliberate, so it is asserted by name."""
    for name in ("operator_present_and_acknowledged", "strategy_enabled",
                 "protection_grounding_complete"):
        assert EXPECTED_EVIDENCE[name] == {"OPERATOR_ATTESTED"}, name
    for name in ("broker_state_verified_fresh", "recovery_consistent",
                 "risk_within_limits", "kill_switch_disengaged",
                 "market_session_valid"):
        assert EXPECTED_EVIDENCE[name] == {"MEASURED"}, name


@pytest.mark.parametrize("name", REQUIRED_PRECONDITIONS)
def test_a_wrong_source_fails_that_precondition(name):
    wrong = next(
        source for source in EvidenceSource
        if source.value not in EXPECTED_EVIDENCE[name]
    )
    assert name in preconditions(
        **{name: att(name, source=wrong)}
    ).unmet(current_monotonic=NOW)


def test_an_unknown_precondition_name_is_never_acceptable():
    assert evidence_is_acceptable("not_a_precondition", att("strategy_enabled")) is False
    assert evidence_is_acceptable("", att("strategy_enabled")) is False


@pytest.mark.parametrize("bad", ["MEASURED", None, 3, True])
def test_an_invalid_evidence_source_is_rejected(bad):
    with pytest.raises(ExecutionGateError):
        Attestation(satisfied=True, source=bad, recorded_monotonic=FRESH)


# --------------------------------------------------------------------------
# 5. False, stale, future, and malformed attestations.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", REQUIRED_PRECONDITIONS)
def test_a_false_attestation_fails_that_precondition(name):
    record = preconditions(**{name: att(name, satisfied=False)})
    assert record.unmet(current_monotonic=NOW) == (name,)


@pytest.mark.parametrize("name", REQUIRED_PRECONDITIONS)
def test_a_stale_attestation_fails_that_precondition(name):
    stale = NOW - MAXIMUM_ATTESTATION_AGE_SECONDS - 1.0
    record = preconditions(**{name: att(name, at=stale)})
    assert record.unmet(current_monotonic=NOW) == (name,)


@pytest.mark.parametrize("name", REQUIRED_PRECONDITIONS)
def test_a_future_dated_attestation_fails_that_precondition(name):
    """A clock or a record is wrong; neither justifies trusting it."""
    record = preconditions(**{name: att(name, at=NOW + 1.0)})
    assert record.unmet(current_monotonic=NOW) == (name,)


def test_the_maximum_age_boundary_is_exact():
    exactly = NOW - MAXIMUM_ATTESTATION_AGE_SECONDS
    just_past = exactly - 0.001
    assert preconditions(
        strategy_enabled=att("strategy_enabled", at=exactly)
    ).all_met(current_monotonic=NOW) is True
    assert "strategy_enabled" in preconditions(
        strategy_enabled=att("strategy_enabled", at=just_past)
    ).unmet(current_monotonic=NOW)


def test_an_attestation_recorded_exactly_now_is_fresh():
    assert preconditions(
        strategy_enabled=att("strategy_enabled", at=NOW)
    ).all_met(current_monotonic=NOW) is True


def test_the_age_bound_is_five_minutes():
    assert MAXIMUM_ATTESTATION_AGE_SECONDS == 300.0


@pytest.mark.parametrize("value", ["1000", None, [], True, object()])
def test_a_malformed_timestamp_is_rejected(value):
    with pytest.raises(ExecutionGateError):
        Attestation(satisfied=True, source=EvidenceSource.MEASURED,
                    recorded_monotonic=value)


def test_a_malformed_current_time_is_refused():
    for bad in ("now", None, True, []):
        with pytest.raises(ExecutionGateError):
            open_gate(preconditions(), current_monotonic=bad)


def test_current_monotonic_is_required_not_defaulted():
    """A default clock would let a caller omit what makes freshness mean anything."""
    with pytest.raises(TypeError):
        open_gate(preconditions())


# --------------------------------------------------------------------------
# 6. Missing and contradictory input.
# --------------------------------------------------------------------------


def test_there_are_no_default_preconditions():
    for field in dataclasses.fields(GatePreconditions):
        assert field.default is dataclasses.MISSING, field.name
        assert field.default_factory is dataclasses.MISSING, field.name


@pytest.mark.parametrize("name", REQUIRED_PRECONDITIONS)
def test_omitting_any_single_precondition_is_refused(name):
    values = {other: att(other) for other in REQUIRED_PRECONDITIONS if other != name}
    with pytest.raises(TypeError):
        GatePreconditions(**values)


def test_omitting_everything_is_refused():
    with pytest.raises(TypeError):
        GatePreconditions()


def test_contradictory_evidence_fails_closed():
    """Satisfied but badly sourced, and well sourced but false, both fail."""
    badly_sourced = preconditions(
        kill_switch_disengaged=att(
            "kill_switch_disengaged", satisfied=True,
            source=EvidenceSource.SELF_DECLARED,
        )
    )
    well_sourced_false = preconditions(
        kill_switch_disengaged=att("kill_switch_disengaged", satisfied=False)
    )
    for record in (badly_sourced, well_sourced_false):
        assert "kill_switch_disengaged" in record.unmet(current_monotonic=NOW)
        assert gate(record).is_open is False


def test_a_field_with_several_defects_is_reported_once():
    """Multiple defects on one field report it once, not three times."""
    record = preconditions(
        risk_within_limits=Attestation(
            satisfied=False, source=EvidenceSource.SELF_DECLARED,
            recorded_monotonic=NOW - 9999.0,
        )
    )
    assert record.unmet(current_monotonic=NOW) == ("risk_within_limits",)


@pytest.mark.parametrize("name", REQUIRED_PRECONDITIONS)
def test_each_precondition_alone_can_close_the_gate(name):
    record = preconditions(**{name: att(name, satisfied=False)})
    assert record.unmet(current_monotonic=NOW) == (name,)
    assert record.all_met(current_monotonic=NOW) is False


def test_the_required_precondition_list_matches_the_dataclass():
    names = tuple(f.name for f in dataclasses.fields(GatePreconditions))
    assert names == REQUIRED_PRECONDITIONS
    assert len(REQUIRED_PRECONDITIONS) == 9


def test_the_precondition_set_covers_the_expected_ground():
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


def test_strategy_enabled_is_a_precondition_not_an_assumption():
    assert "strategy_enabled" in REQUIRED_PRECONDITIONS


# --------------------------------------------------------------------------
# 7. Decisions never authorize.
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


def test_no_gate_result_can_enable_the_strategy():
    """The gate reports; it holds no switch."""
    decision = gate()
    for attribute in ("enable", "activate", "strategy", "execute", "place"):
        assert not hasattr(decision, attribute)
    names = {f.name for f in dataclasses.fields(GateDecision)}
    assert not any(
        word in name for name in names
        for word in ("enable", "activate", "strategy", "execute")
    )


def test_the_repository_still_has_strategy_disabled():
    from src.config import load_config

    assert load_config()["strategy"]["enabled"] is False


def test_the_adapter_protection_mapping_is_untouched():
    from src.live_order_state_model import ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES

    before = frozenset(ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES)
    gate()
    assert frozenset(ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES) == before == frozenset()


def test_a_malformed_precondition_record_closes_rather_than_raises():
    assert gate("everything is fine").verdict is GateVerdict.CLOSED_MALFORMED_INPUT


def test_an_invalid_mode_is_refused():
    with pytest.raises(ExecutionGateError):
        open_gate(preconditions(), mode="SUPERVISED", current_monotonic=NOW)


def test_the_explanation_names_what_is_missing():
    assert "ACTIVATION_APPROVALS" in AUTHORITY_EXPLANATION
    assert "empty" in AUTHORITY_EXPLANATION


def test_the_supervised_mode_is_present():
    assert GateMode.SUPERVISED.value == "SUPERVISED"


# --------------------------------------------------------------------------
# 8. Privacy.
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
    decision = gate(preconditions(
        risk_within_limits=att("risk_within_limits", satisfied=False),
        kill_switch_disengaged=att("kill_switch_disengaged", satisfied=False),
    ))
    facts = decision.sanitized_facts()
    assert facts["unmet_precondition_bucket"] == "many"
    for name in REQUIRED_PRECONDITIONS:
        assert name not in repr(facts)


def test_no_evidence_source_or_timestamp_reaches_the_projection():
    facts = gate().sanitized_facts()
    rendered = repr(facts)
    for source in EvidenceSource:
        assert source.value not in rendered
    assert str(FRESH) not in rendered


def test_the_decision_has_no_field_that_could_carry_broker_data():
    names = {f.name for f in dataclasses.fields(GateDecision)}
    for forbidden in ("symbol", "price", "quantity", "account", "identifier", "payload"):
        assert not any(forbidden in name for name in names), forbidden


def test_facts_survive_the_commissioning_privacy_sanitizer():
    from src.one_share_commissioning import validate_sanitized_evidence

    validate_sanitized_evidence(gate().sanitized_facts())


# --------------------------------------------------------------------------
# 9. Independence and inertness.
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
    """It takes decided facts rather than re-deriving them."""
    assert not any(name.startswith("src") for name in _imports(MODULE_PATH))


def test_no_broker_manifest_checkpoint_or_credential_path_exists():
    source = MODULE_PATH.read_text(encoding="utf-8")
    for token in ("robinhood", "call_tool", "place_equity", "cancel_equity",
                  "manifest", "local.yaml", ".rh_tokens", "credential",
                  "password", "getenv", "environ", "urlopen", "http://"):
        assert token not in source.lower(), token


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


def test_the_gate_reads_no_wall_clock():
    """Time is injected, which is what makes staleness testable."""
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "datetime.now(" not in source
    assert "time.time(" not in source
    assert "time.monotonic(" not in source


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


# --------------------------------------------------------------------------
# 10. The trust boundary, stated rather than assumed.
# --------------------------------------------------------------------------


def test_the_attestation_timestamp_is_caller_supplied_and_that_is_documented():
    """A known and deliberate limit of an offline gate.

    Nothing here can verify that an attestation's timestamp or source is
    truthful — a caller determined to lie can stamp everything `now` and claim
    `OPERATOR_ATTESTED`. The model raises the bar from "pass True" to "state a
    specific provenance and a fresh time", which makes the shape of such a lie
    explicit and reviewable, but it does not make it impossible. Binding
    evidence to its producer needs signed attestations, which is a later
    milestone.

    What keeps this safe today is that the gate is inert regardless: a fully
    fabricated record still yields CLOSED_ACTIVATION_NOT_APPROVED.
    """
    fabricated = GatePreconditions(**{
        name: Attestation(
            satisfied=True,
            source=EvidenceSource(sorted(EXPECTED_EVIDENCE[name])[0]),
            recorded_monotonic=NOW,
        )
        for name in REQUIRED_PRECONDITIONS
    })
    assert fabricated.unmet(current_monotonic=NOW) == ()
    decision = open_gate(fabricated, current_monotonic=NOW)
    assert decision.verdict is GateVerdict.CLOSED_ACTIVATION_NOT_APPROVED
    assert decision.is_open is False
    assert decision.permits_automated_action is False


def test_the_module_docstring_records_why_a_bare_true_is_not_evidence():
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "a bare `True` is not evidence" in source
    assert "SELF_DECLARED" in source


# --------------------------------------------------------------------------
# 11. Limited unattended operation (PR #53).
# --------------------------------------------------------------------------

from src.execution_gates import (  # noqa: E402
    UNATTENDED_PRECONDITIONS,
    UnattendedPreconditions,
    open_unattended_gate,
)

#: Independently written, like EXPECTED_EVIDENCE above. Not read from
#: REQUIRED_EVIDENCE, which is the mapping these tests are validating.
EXPECTED_UNATTENDED_EVIDENCE = {
    "capital_tier_approved": {"OPERATOR_ATTESTED"},
    "deployment_not_expired": {"OPERATOR_ATTESTED"},
    "within_permitted_trading_window": {"MEASURED"},
    "no_unresolved_operator_action": {"OPERATOR_ATTESTED"},
    "supervised_evidence_reviewed": {"OPERATOR_ATTESTED"},
    "alerting_verified_reachable": {"TESTED"},
    "kill_switch_remotely_operable": {"TESTED"},
    "automatic_suspension_armed": {"TESTED"},
}


def unattended_source(name: str) -> EvidenceSource:
    return EvidenceSource(sorted(EXPECTED_UNATTENDED_EVIDENCE[name])[0])


def uatt(name: str, *, satisfied: bool = True, source=None, at: float = FRESH):
    return Attestation(
        satisfied=satisfied,
        source=source if source is not None else unattended_source(name),
        recorded_monotonic=at,
    )


def unattended(**overrides) -> UnattendedPreconditions:
    values = {name: uatt(name) for name in UNATTENDED_PRECONDITIONS}
    values.update(overrides)
    return UnattendedPreconditions(**values)


def ugate(pre=None, un=None, *, current_monotonic=NOW):
    return open_unattended_gate(
        pre if pre is not None else preconditions(),
        un if un is not None else unattended(),
        current_monotonic=current_monotonic,
    )


def test_unattended_mode_exists_and_is_not_approved():
    assert GateMode.LIMITED_UNATTENDED not in ACTIVATION_APPROVALS


def test_the_unattended_gate_cannot_open_with_perfect_evidence():
    decision = ugate()
    assert decision.verdict is GateVerdict.CLOSED_ACTIVATION_NOT_APPROVED
    assert decision.is_open is False
    assert decision.permits_automated_action is False
    assert decision.unmet_precondition_count == 0


def test_unattended_requires_strictly_more_than_supervised():
    """The absence of a human is the reason to require more, not less."""
    assert set(UNATTENDED_PRECONDITIONS).isdisjoint(REQUIRED_PRECONDITIONS)
    assert len(REQUIRED_PRECONDITIONS) + len(UNATTENDED_PRECONDITIONS) == 17


# --- caller booleans cannot reach eligibility -----------------------------


@pytest.mark.parametrize("name", UNATTENDED_PRECONDITIONS)
def test_a_raw_boolean_is_rejected_for_every_unattended_precondition(name):
    with pytest.raises(ExecutionGateError):
        unattended(**{name: True})
    with pytest.raises(ExecutionGateError):
        unattended(**{name: False})


def test_a_fully_boolean_unattended_record_cannot_be_constructed():
    with pytest.raises(ExecutionGateError):
        UnattendedPreconditions(**{name: True for name in UNATTENDED_PRECONDITIONS})


@pytest.mark.parametrize("name", UNATTENDED_PRECONDITIONS)
def test_self_declared_is_rejected_for_every_unattended_precondition(name):
    record = unattended(**{name: uatt(name, source=EvidenceSource.SELF_DECLARED)})
    assert name in record.unmet(current_monotonic=NOW)


def test_an_entirely_self_declared_unattended_record_satisfies_nothing():
    record = UnattendedPreconditions(**{
        name: uatt(name, source=EvidenceSource.SELF_DECLARED)
        for name in UNATTENDED_PRECONDITIONS
    })
    assert len(record.unmet(current_monotonic=NOW)) == len(UNATTENDED_PRECONDITIONS)


def test_eligibility_cannot_be_reached_from_caller_booleans_alone():
    """The whole point of the mode: nobody is watching, so nothing is taken
    on trust."""
    self_declared_supervised = GatePreconditions(**{
        name: att(name, source=EvidenceSource.SELF_DECLARED)
        for name in REQUIRED_PRECONDITIONS
    })
    self_declared_unattended = UnattendedPreconditions(**{
        name: uatt(name, source=EvidenceSource.SELF_DECLARED)
        for name in UNATTENDED_PRECONDITIONS
    })
    decision = ugate(self_declared_supervised, self_declared_unattended)
    assert decision.is_open is False
    assert decision.unmet_precondition_count == 17


# --- per-field evidence rules --------------------------------------------


@pytest.mark.parametrize("name", UNATTENDED_PRECONDITIONS)
def test_each_unattended_precondition_accepts_exactly_its_expected_sources(name):
    expected = {EvidenceSource(v) for v in EXPECTED_UNATTENDED_EVIDENCE[name]}
    actual = {
        source for source in EvidenceSource
        if evidence_is_acceptable(name, uatt(name, source=source))
    }
    assert actual == expected


def test_the_independent_unattended_table_is_complete():
    assert set(EXPECTED_UNATTENDED_EVIDENCE) == set(UNATTENDED_PRECONDITIONS)


def test_machinery_that_must_fire_unattended_requires_a_test_not_a_claim():
    """A component asserting its own readiness proves nothing."""
    for name in ("alerting_verified_reachable", "kill_switch_remotely_operable",
                 "automatic_suspension_armed"):
        assert EXPECTED_UNATTENDED_EVIDENCE[name] == {"TESTED"}, name
        assert evidence_is_acceptable(
            name, uatt(name, source=EvidenceSource.OPERATOR_ATTESTED)
        ) is False
        assert evidence_is_acceptable(
            name, uatt(name, source=EvidenceSource.MEASURED)
        ) is False


def test_operator_decisions_require_a_human():
    for name in ("capital_tier_approved", "deployment_not_expired",
                 "no_unresolved_operator_action", "supervised_evidence_reviewed"):
        assert EXPECTED_UNATTENDED_EVIDENCE[name] == {"OPERATOR_ATTESTED"}, name
        assert evidence_is_acceptable(
            name, uatt(name, source=EvidenceSource.MEASURED)
        ) is False


@pytest.mark.parametrize("name", UNATTENDED_PRECONDITIONS)
def test_a_wrong_source_fails_that_unattended_precondition(name):
    wrong = next(
        s for s in EvidenceSource
        if s.value not in EXPECTED_UNATTENDED_EVIDENCE[name]
    )
    assert name in unattended(**{name: uatt(name, source=wrong)}).unmet(
        current_monotonic=NOW
    )


# --- false, stale, future -------------------------------------------------


@pytest.mark.parametrize("name", UNATTENDED_PRECONDITIONS)
def test_a_false_unattended_attestation_fails(name):
    assert unattended(**{name: uatt(name, satisfied=False)}).unmet(
        current_monotonic=NOW
    ) == (name,)


@pytest.mark.parametrize("name", UNATTENDED_PRECONDITIONS)
def test_a_stale_unattended_attestation_fails(name):
    stale = NOW - MAXIMUM_ATTESTATION_AGE_SECONDS - 1.0
    assert unattended(**{name: uatt(name, at=stale)}).unmet(
        current_monotonic=NOW
    ) == (name,)


@pytest.mark.parametrize("name", UNATTENDED_PRECONDITIONS)
def test_a_future_dated_unattended_attestation_fails(name):
    assert unattended(**{name: uatt(name, at=NOW + 1.0)}).unmet(
        current_monotonic=NOW
    ) == (name,)


def test_the_unattended_age_boundary_is_exact():
    exactly = NOW - MAXIMUM_ATTESTATION_AGE_SECONDS
    assert unattended(
        capital_tier_approved=uatt("capital_tier_approved", at=exactly)
    ).all_met(current_monotonic=NOW) is True
    assert "capital_tier_approved" in unattended(
        capital_tier_approved=uatt("capital_tier_approved", at=exactly - 0.001)
    ).unmet(current_monotonic=NOW)


def test_unattended_current_monotonic_is_required():
    with pytest.raises(TypeError):
        open_unattended_gate(preconditions(), unattended())


def test_a_malformed_unattended_current_time_is_refused():
    for bad in ("now", None, True, []):
        with pytest.raises(ExecutionGateError):
            open_unattended_gate(preconditions(), unattended(), current_monotonic=bad)


# --- supervised conditions still apply ------------------------------------


@pytest.mark.parametrize("name", REQUIRED_PRECONDITIONS)
def test_unattended_still_requires_every_supervised_precondition(name):
    decision = ugate(preconditions(**{name: att(name, satisfied=False)}), unattended())
    assert decision.is_open is False
    assert decision.unmet_precondition_count == 1


def test_operator_acknowledgement_is_still_required_unattended():
    """Unattended means no human watches the session, not that none authorized
    the deployment. The acknowledgement moves earlier; it does not vanish."""
    assert "operator_present_and_acknowledged" in REQUIRED_PRECONDITIONS
    decision = ugate(
        preconditions(operator_present_and_acknowledged=att(
            "operator_present_and_acknowledged", satisfied=False
        )),
        unattended(),
    )
    assert decision.unmet_precondition_count == 1


@pytest.mark.parametrize("name", UNATTENDED_PRECONDITIONS)
def test_each_unattended_precondition_alone_registers_as_unmet(name):
    decision = ugate(preconditions(), unattended(**{name: uatt(name, satisfied=False)}))
    assert decision.unmet_precondition_count == 1
    assert decision.is_open is False


def test_the_unmet_count_spans_both_records():
    decision = ugate(
        preconditions(risk_within_limits=att("risk_within_limits", satisfied=False)),
        unattended(alerting_verified_reachable=uatt(
            "alerting_verified_reachable", satisfied=False
        )),
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


# --- inertness ------------------------------------------------------------


def test_no_combination_of_unattended_preconditions_opens_the_gate():
    """All 256 combinations, with supervised fully satisfied and well sourced."""
    checked = 0
    for combination in itertools.product(
        [True, False], repeat=len(UNATTENDED_PRECONDITIONS)
    ):
        values = {
            name: uatt(name, satisfied=flag)
            for name, flag in zip(UNATTENDED_PRECONDITIONS, combination)
        }
        decision = ugate(preconditions(), UnattendedPreconditions(**values))
        assert decision.is_open is False
        assert decision.permits_automated_action is False
        checked += 1
    assert checked == 256


def test_no_unattended_evidence_source_combination_opens_the_gate():
    for name in UNATTENDED_PRECONDITIONS:
        for source in EvidenceSource:
            assert ugate(
                preconditions(), unattended(**{name: uatt(name, source=source)})
            ).is_open is False, (name, source)


def test_an_open_unattended_decision_cannot_be_constructed():
    with pytest.raises(ExecutionGateError):
        GateDecision(GateVerdict.OPEN, GateMode.LIMITED_UNATTENDED)


def test_there_are_no_default_unattended_preconditions():
    for field in dataclasses.fields(UnattendedPreconditions):
        assert field.default is dataclasses.MISSING, field.name
    with pytest.raises(TypeError):
        UnattendedPreconditions()


@pytest.mark.parametrize("name", UNATTENDED_PRECONDITIONS)
def test_omitting_any_single_unattended_precondition_is_refused(name):
    values = {o: uatt(o) for o in UNATTENDED_PRECONDITIONS if o != name}
    with pytest.raises(TypeError):
        UnattendedPreconditions(**values)


def test_malformed_unattended_input_closes_rather_than_raises():
    assert ugate(preconditions(), "fine").verdict is GateVerdict.CLOSED_MALFORMED_INPUT
    assert ugate("fine", unattended()).verdict is GateVerdict.CLOSED_MALFORMED_INPUT


def test_the_readiness_report_counts_both_requirement_sets():
    report = gate_readiness_report()
    assert report["required_precondition_count"] == 9
    assert report["unattended_precondition_count"] == 8
    assert report["total_unattended_requirement_count"] == 17
    assert report["gate_is_openable"] is False
    assert report["self_declared_evidence_accepted_anywhere"] is False


def test_no_unattended_result_can_enable_the_strategy():
    decision = ugate()
    for attribute in ("enable", "activate", "strategy", "execute", "place"):
        assert not hasattr(decision, attribute)


def test_unattended_facts_are_allowlisted():
    facts = ugate().sanitized_facts()
    assert set(facts) == set(ALLOWED_FACT_VALUES)
    for key, value in facts.items():
        assert value in ALLOWED_FACT_VALUES[key], (key, value)
    assert facts["gate_mode"] == "LIMITED_UNATTENDED"


def test_the_mode_vocabulary_is_closed_with_two_modes():
    assert {mode.value for mode in GateMode} == {"SUPERVISED", "LIMITED_UNATTENDED"}
    assert ACTIVATION_APPROVALS == frozenset()
