"""Hard risk controls — limits, duplicates, staleness, kill switch.

Every test is local and offline.

The properties that matter most:

1. the agent cannot raise its own limits — structurally, not by convention;
2. denial is the default, and no check can be skipped by an earlier pass;
3. staying inside limits is never permission to act.
"""

from __future__ import annotations

import ast
import dataclasses
import pathlib
from decimal import Decimal

import pytest

import src.risk_limits as risk
from src.risk_limits import (
    AUTHORITY_EXPLANATION,
    DENIAL_DECISIONS,
    AccountRiskState,
    ActionLedger,
    ActionProposal,
    KillSwitchState,
    RiskAssessment,
    RiskDecision,
    RiskLimitError,
    RiskLimits,
    evaluate,
)

MODULE_PATH = pathlib.Path(risk.__file__)

PINNED_MODULES = (
    "read_only_observation_runtime",
    "read_only_broker_state",
    "read_only_authorization_manifest",
    "live_order_state_model",
)


def limits(**overrides) -> RiskLimits:
    values = {
        "maximum_position_quantity": 10,
        "maximum_order_value": Decimal("1000"),
        "maximum_daily_loss": Decimal("45"),
        "maximum_open_positions": 1,
    }
    values.update(overrides)
    return RiskLimits(**values)


def proposal(**overrides) -> ActionProposal:
    values = {
        "idempotency_key": "act-1",
        "quantity": 1,
        "order_value": Decimal("100"),
        "observed_monotonic": 100.0,
    }
    values.update(overrides)
    return ActionProposal(**values)


def state(**overrides) -> AccountRiskState:
    values = {"realized_daily_loss": Decimal("0"), "open_position_count": 0}
    values.update(overrides)
    return AccountRiskState(**values)


def assess(*, prop=None, lim=None, st=None, switch=KillSwitchState.DISENGAGED,
           ledger=None, now=101.0, **kwargs):
    return evaluate(
        prop or proposal(),
        lim or limits(),
        st or state(),
        kill_switch=switch,
        ledger=ledger if ledger is not None else ActionLedger(),
        current_monotonic=now,
        **kwargs,
    )


# --------------------------------------------------------------------------
# 1. The agent cannot raise its own limits.
# --------------------------------------------------------------------------


def test_limits_are_frozen():
    value = limits()
    with pytest.raises(dataclasses.FrozenInstanceError):
        value.maximum_order_value = Decimal("999999")
    with pytest.raises(dataclasses.FrozenInstanceError):
        value.maximum_position_quantity = 10_000


def test_limits_expose_no_widening_method():
    """Structural: there is nothing to call, not merely nothing that is called."""
    widening = {"increase", "raise", "widen", "grow", "extend", "escalate",
                "with_higher", "update", "set"}
    for name in dir(RiskLimits):
        if name.startswith("_"):
            continue
        tokens = set(name.lower().split("_"))
        assert not (tokens & widening), name


def test_no_module_function_mutates_a_limit():
    """No assignment anywhere targets a limit attribute."""
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Attribute):
                    assert not target.attr.startswith("maximum_"), target.attr


def test_there_are_no_default_limit_values():
    """A default limit is a policy nobody approved."""
    for field in dataclasses.fields(RiskLimits):
        assert field.default is dataclasses.MISSING, field.name
        assert field.default_factory is dataclasses.MISSING, field.name
    with pytest.raises(TypeError):
        RiskLimits()


@pytest.mark.parametrize(
    "field,value",
    [
        ("maximum_position_quantity", 0), ("maximum_position_quantity", -1),
        ("maximum_position_quantity", True), ("maximum_position_quantity", 1.5),
        ("maximum_open_positions", 0), ("maximum_open_positions", -3),
        ("maximum_order_value", Decimal("0")), ("maximum_order_value", Decimal("-1")),
        ("maximum_daily_loss", Decimal("0")), ("maximum_daily_loss", Decimal("-5")),
    ],
)
def test_nonsensical_limits_are_refused(field, value):
    with pytest.raises(RiskLimitError):
        limits(**{field: value})


@pytest.mark.parametrize("value", [1000.0, 1e3, float("nan"), float("inf")])
def test_a_float_limit_is_refused_rather_than_converted(value):
    """Implicit float to Decimal hides imprecision instead of removing it."""
    with pytest.raises(RiskLimitError):
        limits(maximum_order_value=value)


@pytest.mark.parametrize("value", [Decimal("1000"), 1000, "1000", "1000.50"])
def test_exact_money_types_are_accepted(value):
    assert limits(maximum_order_value=value).maximum_order_value > 0


def test_a_non_numeric_limit_is_refused():
    with pytest.raises(RiskLimitError):
        limits(maximum_order_value="not-a-number")


# --------------------------------------------------------------------------
# 2. Kill switch.
# --------------------------------------------------------------------------


def test_the_kill_switch_denies_everything():
    result = assess(switch=KillSwitchState.ENGAGED)
    assert result.decision is RiskDecision.DENIED_KILL_SWITCH_ENGAGED
    assert result.is_denial is True


def test_the_kill_switch_is_checked_before_anything_else():
    """A suspended system should not be computing position sizes.

    Everything else about this proposal is also wrong; the switch must still
    be the reported reason, or an operator learns the wrong thing about why
    the system stopped.
    """
    ledger = ActionLedger()
    ledger.record("act-1", 1)
    result = assess(
        prop=proposal(quantity=9999, order_value=Decimal("999999"),
                      observed_monotonic=0.0),
        st=state(realized_daily_loss=Decimal("100"), open_position_count=5),
        switch=KillSwitchState.ENGAGED,
        ledger=ledger,
        now=99999.0,
    )
    assert result.decision is RiskDecision.DENIED_KILL_SWITCH_ENGAGED


def test_the_kill_switch_has_exactly_two_states():
    assert {s.value for s in KillSwitchState} == {"ENGAGED", "DISENGAGED"}


def test_a_missing_kill_switch_state_is_refused():
    with pytest.raises(RiskLimitError):
        assess(switch="ENGAGED")


# --------------------------------------------------------------------------
# 3. Duplicate prevention.
# --------------------------------------------------------------------------


def test_a_replayed_action_is_denied():
    ledger = ActionLedger()
    ledger.record("act-1", 1)
    assert assess(ledger=ledger).decision is RiskDecision.DENIED_DUPLICATE_ACTION


def test_duplicate_detection_precedes_limit_arithmetic():
    """A replayed action is a different problem from an oversized one."""
    ledger = ActionLedger()
    ledger.record("act-1", 1)
    result = assess(
        prop=proposal(quantity=9999, order_value=Decimal("999999")), ledger=ledger
    )
    assert result.decision is RiskDecision.DENIED_DUPLICATE_ACTION


def test_the_ledger_refuses_to_record_a_key_twice():
    ledger = ActionLedger()
    ledger.record("k", 1)
    with pytest.raises(RiskLimitError):
        ledger.record("k", 1)
    assert ledger.recorded_count == 1


def test_the_ledger_has_no_removal_method():
    """"Forget this key" is indistinguishable from "let it happen again"."""
    removal = {"remove", "delete", "clear", "forget", "reset", "pop", "discard"}
    for name in dir(ActionLedger):
        if name.startswith("_"):
            continue
        assert not (set(name.lower().split("_")) & removal), name


@pytest.mark.parametrize("key", ["", "   ", None, 7])
def test_an_invalid_idempotency_key_is_refused(key):
    with pytest.raises(RiskLimitError):
        ActionLedger().has_seen(key)
    with pytest.raises(RiskLimitError):
        proposal(idempotency_key=key)


# --------------------------------------------------------------------------
# 4. Staleness.
# --------------------------------------------------------------------------


def test_stale_data_is_denied():
    assert assess(now=1000.0).decision is RiskDecision.DENIED_STALE_DATA


def test_data_from_the_future_is_denied():
    """As untrustworthy as data that is too old."""
    assert assess(now=50.0).decision is RiskDecision.DENIED_STALE_DATA


def test_data_at_the_age_bound_is_accepted():
    assert assess(now=105.0).decision is RiskDecision.WITHIN_LIMITS


def test_data_one_tick_past_the_bound_is_denied():
    assert assess(now=105.01).decision is RiskDecision.DENIED_STALE_DATA


def test_the_age_bound_is_configurable_but_not_defaulted_away():
    assert assess(now=110.0, maximum_data_age_seconds=20.0).decision is (
        RiskDecision.WITHIN_LIMITS
    )


# --------------------------------------------------------------------------
# 5. Limit breaches.
# --------------------------------------------------------------------------


def test_position_size_breach_is_denied():
    assert assess(prop=proposal(quantity=11)).decision is (
        RiskDecision.DENIED_POSITION_SIZE
    )


def test_position_size_at_the_ceiling_is_allowed():
    assert assess(prop=proposal(quantity=10)).decision is RiskDecision.WITHIN_LIMITS


def test_quantity_is_judged_against_the_resulting_position():
    """Self-review finding: orders used to be judged in isolation.

    Two individually legal orders could stack past the ceiling because the
    check ignored what was already held.
    """
    result = assess(
        prop=proposal(quantity=6),
        st=state(open_position_quantity=6),          # 6 + 6 > 10
    )
    assert result.decision is RiskDecision.DENIED_POSITION_SIZE


def test_topping_up_exactly_to_the_ceiling_is_allowed():
    assert assess(
        prop=proposal(quantity=4), st=state(open_position_quantity=6)
    ).decision is RiskDecision.WITHIN_LIMITS


def test_one_unit_past_the_ceiling_via_an_existing_position_is_denied():
    assert assess(
        prop=proposal(quantity=5), st=state(open_position_quantity=6)
    ).decision is RiskDecision.DENIED_POSITION_SIZE


@pytest.mark.parametrize("held", [-1, 1.5, True, "1"])
def test_an_invalid_held_quantity_is_refused(held):
    with pytest.raises(RiskLimitError):
        state(open_position_quantity=held)


def test_order_value_breach_is_denied():
    assert assess(prop=proposal(order_value=Decimal("1000.01"))).decision is (
        RiskDecision.DENIED_ORDER_VALUE
    )


def test_order_value_at_the_ceiling_is_allowed():
    assert assess(prop=proposal(order_value=Decimal("1000"))).decision is (
        RiskDecision.WITHIN_LIMITS
    )


def test_a_cent_over_the_ceiling_is_caught_exactly():
    """The reason money is Decimal: this comparison must not drift."""
    assert assess(
        lim=limits(maximum_order_value=Decimal("2680.00")),
        prop=proposal(order_value=Decimal("2680.01")),
    ).decision is RiskDecision.DENIED_ORDER_VALUE
    assert assess(
        lim=limits(maximum_order_value=Decimal("2680.00")),
        prop=proposal(order_value=Decimal("2680.00")),
    ).decision is RiskDecision.WITHIN_LIMITS


def test_daily_loss_at_the_limit_is_already_too_far():
    """The next loss would exceed it, so at the limit must stop."""
    assert assess(st=state(realized_daily_loss=Decimal("45"))).decision is (
        RiskDecision.DENIED_DAILY_LOSS
    )


def test_daily_loss_below_the_limit_is_allowed():
    assert assess(st=state(realized_daily_loss=Decimal("44.99"))).decision is (
        RiskDecision.WITHIN_LIMITS
    )


def test_open_position_count_at_the_limit_denies_another():
    assert assess(st=state(open_position_count=1)).decision is (
        RiskDecision.DENIED_OPEN_POSITION_COUNT
    )


def test_a_negative_loss_magnitude_is_refused():
    """Guessing the sign convention is how a limit gets silently inverted."""
    with pytest.raises(RiskLimitError):
        state(realized_daily_loss=Decimal("-10"))


@pytest.mark.parametrize("count", [-1, 1.5, True, "1"])
def test_an_invalid_open_position_count_is_refused(count):
    with pytest.raises(RiskLimitError):
        state(open_position_count=count)


# --------------------------------------------------------------------------
# 6. Proposals.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("quantity", [0, -1, 1.5, True, "1", None])
def test_an_invalid_quantity_is_refused(quantity):
    with pytest.raises(RiskLimitError):
        proposal(quantity=quantity)


@pytest.mark.parametrize("value", [Decimal("0"), Decimal("-1")])
def test_a_non_positive_order_value_is_refused(value):
    with pytest.raises(RiskLimitError):
        proposal(order_value=value)


def test_a_non_proposal_is_denied_not_crashed():
    ledger = ActionLedger()
    result = evaluate(
        "not a proposal", limits(), state(),
        kill_switch=KillSwitchState.DISENGAGED, ledger=ledger,
        current_monotonic=1.0,
    )
    assert result.decision is RiskDecision.DENIED_MALFORMED_PROPOSAL


def test_missing_collaborators_are_refused():
    with pytest.raises(RiskLimitError):
        evaluate(proposal(), "limits", state(),
                 kill_switch=KillSwitchState.DISENGAGED,
                 ledger=ActionLedger(), current_monotonic=101.0)
    with pytest.raises(RiskLimitError):
        evaluate(proposal(), limits(), state(),
                 kill_switch=KillSwitchState.DISENGAGED,
                 ledger=None, current_monotonic=101.0)
    with pytest.raises(RiskLimitError):
        evaluate(proposal(), limits(), state(),
                 kill_switch=KillSwitchState.DISENGAGED,
                 ledger=ActionLedger(), current_monotonic="now")


# --------------------------------------------------------------------------
# 7. Authority and fail-closed invariants.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("decision", list(RiskDecision))
def test_no_decision_permits_automated_action(decision):
    assert RiskAssessment(decision).permits_automated_action is False


def test_a_permitting_assessment_cannot_be_constructed():
    with pytest.raises(RiskLimitError):
        RiskAssessment(RiskDecision.WITHIN_LIMITS, permits_automated_action=True)


def test_within_limits_is_the_only_non_denial():
    assert DENIAL_DECISIONS == set(RiskDecision) - {RiskDecision.WITHIN_LIMITS}
    assert len(DENIAL_DECISIONS) == 8
    assert RiskAssessment(RiskDecision.WITHIN_LIMITS).is_denial is False
    for decision in DENIAL_DECISIONS:
        assert RiskAssessment(decision).is_denial is True


def test_the_explanation_states_where_authority_comes_from():
    assert "not permission to act" in AUTHORITY_EXPLANATION
    assert "does not exist yet" in AUTHORITY_EXPLANATION


def test_a_clean_proposal_passes_every_check():
    assert assess().decision is RiskDecision.WITHIN_LIMITS


# --------------------------------------------------------------------------
# 8. Privacy.
# --------------------------------------------------------------------------

ALLOWED_FACT_VALUES = {
    "risk_decision": {decision.value for decision in RiskDecision},
    "is_denial": {"true", "false"},
    "permits_automated_action": {"false"},
}


def test_risk_facts_are_allowlisted():
    for decision in RiskDecision:
        facts = RiskAssessment(decision).sanitized_facts()
        assert set(facts) == set(ALLOWED_FACT_VALUES)
        for key, value in facts.items():
            assert value in ALLOWED_FACT_VALUES[key], (key, value)


def test_facts_carry_no_amount_key_or_quantity():
    result = assess(
        prop=proposal(idempotency_key="SECRET-KEY-9", quantity=7,
                      order_value=Decimal("1234.56"))
    )
    rendered = repr(result.sanitized_facts())
    for forbidden in ("SECRET-KEY-9", "1234.56", "7"):
        assert forbidden not in rendered


def test_the_assessment_has_no_field_that_could_carry_one():
    names = {f.name for f in dataclasses.fields(RiskAssessment)}
    for forbidden in ("amount", "value", "quantity", "key", "symbol", "price"):
        assert not any(forbidden in name for name in names), forbidden


def test_facts_survive_the_commissioning_privacy_sanitizer():
    from src.one_share_commissioning import validate_sanitized_evidence

    for decision in RiskDecision:
        validate_sanitized_evidence(RiskAssessment(decision).sanitized_facts())


# --------------------------------------------------------------------------
# 9. Independence.
# --------------------------------------------------------------------------


def _imports(path: pathlib.Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_risk_limits_import_no_pinned_checkpoint_file():
    imported = _imports(MODULE_PATH)
    for pinned in PINNED_MODULES:
        assert not any(pinned in name for name in imported), pinned


def test_risk_limits_import_nothing_broker_shaped():
    forbidden = ("adapter", "client", "session", "mcp", "orchestrator",
                 "commissioning", "manifest", "checkpoint", "strategy", "broker")
    for name in _imports(MODULE_PATH):
        assert not any(word in name.lower() for word in forbidden), name


def test_risk_limits_perform_no_io_and_no_network():
    imported = _imports(MODULE_PATH)
    assert imported.isdisjoint(
        {"socket", "http", "urllib", "requests", "httpx", "aiohttp",
         "subprocess", "os", "pathlib"}
    )
    for node in ast.walk(ast.parse(MODULE_PATH.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.attr if isinstance(func, ast.Attribute)
                else func.id if isinstance(func, ast.Name)
                else None
            )
            assert name not in {"open", "urlopen", "read_text", "write_text"}


def test_risk_limits_read_no_wall_clock():
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "datetime.now(" not in source
    assert "time.time(" not in source
    assert "time.monotonic(" not in source


def test_risk_limits_define_no_write_shaped_function():
    forbidden = {"place", "cancel", "submit", "activate", "execute", "send"}
    for node in ast.walk(ast.parse(MODULE_PATH.read_text(encoding="utf-8"))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            tokens = set(node.name.lower().strip("_").split("_"))
            assert not (tokens & forbidden), node.name


def test_no_dollar_amount_is_hard_coded_as_a_permanent_limit():
    """Capital policy: no single amount may define the system."""
    source = MODULE_PATH.read_text(encoding="utf-8")
    for amount in ("2680", "30000", "3000", "15", "45", "120", "300"):
        assert f'Decimal("{amount}")' not in source


# --------------------------------------------------------------------------
# 10. Cumulative exposure (review finding).
# --------------------------------------------------------------------------


def test_several_individually_legal_orders_cannot_cumulatively_exceed():
    """Review finding: they used to.

    Three four-unit orders against a ten-unit ceiling are each legal in
    isolation. With a caller that never updates its reported position, all
    three previously passed and twelve units were approved. The ledger now
    accumulates what it approved, so the caller cannot walk past the ceiling
    one legal step at a time.
    """
    lim = limits(maximum_position_quantity=10)
    ledger = ActionLedger()
    approved = 0
    outcomes = []
    for index in range(3):
        result = assess(
            prop=proposal(idempotency_key=f"a{index}", quantity=4),
            lim=lim,
            st=state(open_position_quantity=0),      # caller never updates
            ledger=ledger,
        )
        outcomes.append(result.decision)
        if result.decision is RiskDecision.WITHIN_LIMITS:
            ledger.record(f"a{index}", 4)
            approved += 4
    assert outcomes == [
        RiskDecision.WITHIN_LIMITS,
        RiskDecision.WITHIN_LIMITS,
        RiskDecision.DENIED_POSITION_SIZE,
    ]
    assert approved == 8
    assert approved <= lim.maximum_position_quantity


def test_the_bound_holds_when_the_caller_does_update_its_position():
    lim = limits(maximum_position_quantity=10)
    ledger = ActionLedger()
    held = 0
    for index in range(2):
        result = assess(
            prop=proposal(idempotency_key=f"b{index}", quantity=4),
            lim=lim, st=state(open_position_quantity=held), ledger=ledger,
        )
        assert result.decision is RiskDecision.WITHIN_LIMITS
        ledger.record(f"b{index}", 4)
        held += 4
    third = assess(
        prop=proposal(idempotency_key="b2", quantity=4),
        lim=lim, st=state(open_position_quantity=held), ledger=ledger,
    )
    assert third.decision is RiskDecision.DENIED_POSITION_SIZE


def test_the_two_exposure_views_are_maxed_not_summed():
    """They usually describe the same orders; summing would double-count."""
    lim = limits(maximum_position_quantity=10)
    ledger = ActionLedger()
    ledger.record("prior", 6)
    # Caller reports the same 6 units it holds. max(6, 6) + 4 = 10, allowed.
    assert assess(
        prop=proposal(quantity=4), lim=lim,
        st=state(open_position_quantity=6), ledger=ledger,
    ).decision is RiskDecision.WITHIN_LIMITS
    # Summing would have given 16 and denied a legitimate top-up.


def test_ledger_exposure_alone_bounds_a_caller_reporting_nothing():
    lim = limits(maximum_position_quantity=10)
    ledger = ActionLedger()
    ledger.record("prior", 9)
    assert assess(
        prop=proposal(quantity=2), lim=lim,
        st=state(open_position_quantity=0), ledger=ledger,
    ).decision is RiskDecision.DENIED_POSITION_SIZE


def test_caller_reported_exposure_alone_bounds_an_empty_ledger():
    lim = limits(maximum_position_quantity=10)
    assert assess(
        prop=proposal(quantity=2), lim=lim,
        st=state(open_position_quantity=9), ledger=ActionLedger(),
    ).decision is RiskDecision.DENIED_POSITION_SIZE


def test_approved_quantity_never_decreases():
    ledger = ActionLedger()
    ledger.record("x", 3)
    ledger.record("y", 4)
    assert ledger.approved_quantity == 7
    assert ledger.recorded_count == 2


def test_recording_requires_an_explicit_approved_quantity():
    """A default of zero would silently restore the gap."""
    ledger = ActionLedger()
    with pytest.raises(TypeError):
        ledger.record("z")


@pytest.mark.parametrize("quantity", [-1, 1.5, True, "4", None])
def test_an_invalid_approved_quantity_is_refused(quantity):
    with pytest.raises(RiskLimitError):
        ActionLedger().record("z", quantity)


def test_a_rejected_proposal_adds_no_exposure():
    """Only approvals accumulate; a denial must not consume headroom."""
    lim = limits(maximum_position_quantity=10)
    ledger = ActionLedger()
    denied = assess(prop=proposal(quantity=99), lim=lim, ledger=ledger)
    assert denied.decision is RiskDecision.DENIED_POSITION_SIZE
    assert ledger.approved_quantity == 0
    assert assess(
        prop=proposal(idempotency_key="next", quantity=10), lim=lim, ledger=ledger
    ).decision is RiskDecision.WITHIN_LIMITS


def test_the_kill_switch_cannot_be_bypassed_by_a_nominally_valid_request():
    """Every other check passes; the switch still wins."""
    for extra in ({}, {"maximum_data_age_seconds": 1e9}):
        result = assess(
            prop=proposal(), switch=KillSwitchState.ENGAGED,
            ledger=ActionLedger(), **extra
        )
        assert result.decision is RiskDecision.DENIED_KILL_SWITCH_ENGAGED
