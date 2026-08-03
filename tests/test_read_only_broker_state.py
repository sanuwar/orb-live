"""Fail-closed read-only broker-state grounding (Milestone 9).

Every test here is pure and local. Nothing constructs a session, reaches a
broker, or touches the network, and the module under test contains no write
call site at all — asserted directly below rather than assumed.

The property that matters most: with no state approved for active protection,
`VERIFIED_ACTIVE_PROTECTION` must be unreachable no matter what a snapshot
claims.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

import src.read_only_broker_state as grounding
from src.live_order_state_model import (
    ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES,
    OrderStateClass,
)
from src.read_only_broker_state import (
    ALLOWED_SNAPSHOT_FACTS,
    BrokerStateDecision,
    CashAvailability,
    MarketSession,
    ObservationOutcome,
    PROTECTION_STATE_CANDIDATES,
    PositionClassification,
    ProtectionObservation,
    ProtectionStateCandidate,
    ReadAvailability,
    ReadOnlyBrokerSnapshot,
    ReadOnlyGroundingError,
    ReadOnlyObservationPlan,
    SnapshotFreshness,
    SnapshotFreshnessPolicy,
    StateProvenance,
    approved_active_protection_states,
    count_bucket,
    decide_broker_state,
    grounding_is_complete,
    proposed_state_promotion,
    validate_snapshot_facts,
)

AVAILABLE = ReadAvailability.AVAILABLE


def snapshot(**overrides) -> ReadOnlyBrokerSnapshot:
    """A complete, fresh, internally consistent snapshot; override one fact."""
    base = dict(
        account_read=AVAILABLE, position_read=AVAILABLE,
        order_read=AVAILABLE, session_read=AVAILABLE,
        cash_availability=CashAvailability.SUFFICIENT_FOR_PLANNED_RISK,
        position=PositionClassification.FLAT,
        market_session=MarketSession.REGULAR_HOURS,
        first_read_monotonic=100.0, last_read_monotonic=100.4,
        evaluated_monotonic=100.8,
    )
    base.update(overrides)
    return ReadOnlyBrokerSnapshot(**base)


def decide(**overrides) -> BrokerStateDecision:
    return decide_broker_state(snapshot(**overrides)).decision


# --- the grounding gap is real and enforced ---------------------------


def test_no_state_is_approved_for_active_protection():
    assert approved_active_protection_states() == frozenset()
    assert grounding_is_complete() is False


def test_module_agrees_with_the_adapter_mapping():
    report = grounding.grounding_consistency_report()
    assert report["agrees_with_adapter"] is True
    assert approved_active_protection_states() == (
        ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES
    )


def test_verified_active_protection_is_unreachable_while_ungrounded():
    """Even a snapshot asserting an approved state cannot force the claim."""
    assert decide(
        position=PositionClassification.LONG_EXPECTED_QUANTITY,
        protection=ProtectionObservation.OBSERVED_APPROVED_STATE,
        protective_candidate_count=1,
        executable_related_sell_count=1,
    ) is BrokerStateDecision.UNKNOWN_BROKER_STATE


def test_every_candidate_state_is_unapproved_and_provenance_recorded():
    for state, candidate in PROTECTION_STATE_CANDIDATES.items():
        assert candidate.normalized_state == state
        assert candidate.is_approved_for_active_protection is False
        assert candidate.provenance in grounding.UNGROUNDED_PROVENANCE
        # Nothing has been asserted about protective meaning.
        assert candidate.can_protect is None
        assert candidate.can_coexist_with_executable_sell is None


@pytest.mark.parametrize(
    "state", ["confirmed", "unconfirmed", "filled", "cancelled"]
)
def test_states_seen_in_live_evidence_are_recorded_as_observed_not_approved(state):
    candidate = PROTECTION_STATE_CANDIDATES[state]
    assert candidate.provenance is StateProvenance.HISTORICAL_SANITIZED_EVIDENCE
    assert candidate.is_approved_for_active_protection is False


@pytest.mark.parametrize("state", ["new", "queued", "pending_cancelled", "voided"])
def test_schema_only_states_are_marked_schema_only(state):
    assert PROTECTION_STATE_CANDIDATES[state].provenance is (
        StateProvenance.SCHEMA_ONLY
    )


def test_an_approved_candidate_must_be_known_to_protect():
    with pytest.raises(ReadOnlyGroundingError):
        ProtectionStateCandidate(
            normalized_state="confirmed",
            provenance=StateProvenance.APPROVED_FOR_ACTIVE_PROTECTION,
        )


def test_candidate_terminal_and_executable_classification():
    assert PROTECTION_STATE_CANDIDATES["filled"].is_terminal is True
    assert PROTECTION_STATE_CANDIDATES["cancelled"].is_terminal is True
    assert PROTECTION_STATE_CANDIDATES["confirmed"].is_executable is True
    # A cancel that has not settled can still be beaten by a fill.
    assert PROTECTION_STATE_CANDIDATES["pending_cancelled"].is_executable is True
    assert PROTECTION_STATE_CANDIDATES["filled"].is_executable is False


def test_a_candidate_state_must_be_normalized_and_non_blank():
    for bad in ("  Confirmed ", "", "   "):
        with pytest.raises(ReadOnlyGroundingError):
            ProtectionStateCandidate(
                normalized_state=bad, provenance=StateProvenance.SCHEMA_ONLY
            )


# --- decision outcomes ------------------------------------------------


def test_flat_with_no_related_orders_is_verified_flat():
    assert decide() is BrokerStateDecision.VERIFIED_FLAT


def test_flat_with_an_executable_sell_is_never_verified_flat():
    assert decide(executable_related_sell_count=1) is (
        BrokerStateDecision.CONFLICTING_EXECUTABLE_ORDERS
    )


def test_position_with_an_unapproved_protective_state_fails_closed():
    assert decide(
        position=PositionClassification.LONG_EXPECTED_QUANTITY,
        protection=ProtectionObservation.OBSERVED_UNAPPROVED_STATE,
        protective_candidate_count=1,
        executable_related_sell_count=1,
    ) is BrokerStateDecision.UNKNOWN_BROKER_STATE


def test_position_with_no_protection_is_unprotected():
    assert decide(
        position=PositionClassification.LONG_EXPECTED_QUANTITY,
        protection=ProtectionObservation.NOT_OBSERVED,
    ) is BrokerStateDecision.UNPROTECTED_POSITION


def test_position_with_terminal_protection_is_unprotected():
    assert decide(
        position=PositionClassification.LONG_EXPECTED_QUANTITY,
        protection=ProtectionObservation.OBSERVED_TERMINAL,
        protective_candidate_count=1,
    ) is BrokerStateDecision.UNPROTECTED_POSITION


def test_multiple_executable_sells_conflict():
    assert decide(
        position=PositionClassification.LONG_EXPECTED_QUANTITY,
        executable_related_sell_count=2,
    ) is BrokerStateDecision.CONFLICTING_EXECUTABLE_ORDERS


def test_ambiguous_protective_candidates_conflict():
    assert decide(
        position=PositionClassification.LONG_EXPECTED_QUANTITY,
        protection=ProtectionObservation.AMBIGUOUS_CANDIDATES,
        protective_candidate_count=2,
    ) is BrokerStateDecision.CONFLICTING_EXECUTABLE_ORDERS


def test_unknown_protective_state_fails_closed():
    assert decide(
        position=PositionClassification.LONG_EXPECTED_QUANTITY,
        protection=ProtectionObservation.OBSERVED_UNKNOWN_STATE,
        protective_candidate_count=1,
    ) is BrokerStateDecision.UNKNOWN_BROKER_STATE


def test_short_and_unexpected_quantities_fail_closed():
    for position in (PositionClassification.SHORT,
                     PositionClassification.LONG_UNEXPECTED_QUANTITY,
                     PositionClassification.UNKNOWN):
        assert decide(position=position) is (
            BrokerStateDecision.UNKNOWN_BROKER_STATE
        )


def test_approved_state_produces_verified_active_protection_when_grounded():
    """The path exists and is correct; only the approval is missing today."""
    assessment = decide_broker_state(
        snapshot(
            position=PositionClassification.LONG_EXPECTED_QUANTITY,
            protection=ProtectionObservation.OBSERVED_APPROVED_STATE,
            protective_candidate_count=1,
            executable_related_sell_count=1,
        ),
        approved_states=frozenset({"confirmed"}),
    )
    assert assessment.decision is BrokerStateDecision.VERIFIED_ACTIVE_PROTECTION
    assert assessment.grounding_complete is True
    assert assessment.is_safe_to_proceed is True


def test_grounded_but_multiple_candidates_still_conflicts():
    assessment = decide_broker_state(
        snapshot(
            position=PositionClassification.LONG_EXPECTED_QUANTITY,
            protection=ProtectionObservation.OBSERVED_APPROVED_STATE,
            protective_candidate_count=2,
            executable_related_sell_count=1,
        ),
        approved_states=frozenset({"confirmed"}),
    )
    assert assessment.decision is (
        BrokerStateDecision.CONFLICTING_EXECUTABLE_ORDERS
    )


def test_only_flat_and_verified_protection_are_safe():
    assert grounding.SAFE_DECISIONS == frozenset({
        BrokerStateDecision.VERIFIED_FLAT,
        BrokerStateDecision.VERIFIED_ACTIVE_PROTECTION,
    })
    for decision in BrokerStateDecision:
        if decision not in grounding.SAFE_DECISIONS:
            assert decision not in grounding.SAFE_DECISIONS


# --- availability, completeness, freshness ----------------------------


@pytest.mark.parametrize(
    "field", ["account_read", "position_read", "order_read", "session_read"]
)
def test_any_unavailable_read_is_broker_read_unavailable(field):
    assert decide(**{field: ReadAvailability.UNAVAILABLE}) is (
        BrokerStateDecision.BROKER_READ_UNAVAILABLE
    )


@pytest.mark.parametrize(
    "field", ["account_read", "position_read", "order_read", "session_read"]
)
def test_any_unattempted_read_is_incomplete(field):
    assert decide(**{field: ReadAvailability.NOT_ATTEMPTED}) is (
        BrokerStateDecision.INCOMPLETE_BROKER_STATE
    )


def test_a_stale_snapshot_is_never_safe():
    assert decide(evaluated_monotonic=200.0) is (
        BrokerStateDecision.STALE_BROKER_STATE
    )


def test_reads_spread_too_far_apart_are_not_one_moment():
    assert decide(last_read_monotonic=104.0, evaluated_monotonic=104.1) is (
        BrokerStateDecision.STALE_BROKER_STATE
    )


@pytest.mark.parametrize(
    "stamps",
    [
        {"first_read_monotonic": None},
        {"last_read_monotonic": None},
        {"evaluated_monotonic": None},
        {"last_read_monotonic": 99.0},          # went backwards
        {"evaluated_monotonic": 100.1, "last_read_monotonic": 100.4},
    ],
)
def test_unusable_timestamps_fail_closed(stamps):
    assert decide(**stamps) is BrokerStateDecision.UNKNOWN_BROKER_STATE


def test_order_change_during_acquisition_fails_closed():
    assert decide(order_state_changed_during_acquisition=True) is (
        BrokerStateDecision.UNKNOWN_BROKER_STATE
    )


def test_contradictory_position_observations_fail_closed():
    assert decide(position_response_self_contradictory=True) is (
        BrokerStateDecision.UNKNOWN_BROKER_STATE
    )
    assert decide(position=PositionClassification.CONTRADICTORY) is (
        BrokerStateDecision.UNKNOWN_BROKER_STATE
    )


def test_a_market_session_change_during_acquisition_fails_closed():
    """Its own field now, rather than being folded into the order flag."""
    assert decide(market_session_changed_during_acquisition=True) is (
        BrokerStateDecision.UNKNOWN_BROKER_STATE
    )


def test_the_three_instability_flags_are_independent():
    """Each one alone is disqualifying; none masks another.

    Before F2 the order flag carried the session signal, so a genuine order
    change and a session rollover were indistinguishable in evidence. Setting
    each independently proves they are now separate channels.
    """
    flags = (
        "order_state_changed_during_acquisition",
        "position_response_self_contradictory",
        "market_session_changed_during_acquisition",
    )
    for flag in flags:
        assert decide(**{flag: True}) is BrokerStateDecision.UNKNOWN_BROKER_STATE
        facts = decide_broker_state(snapshot(**{flag: True})).facts
        assert facts[flag] == "true"
        for other in flags:
            if other != flag:
                assert facts[other] == "false"


def test_session_transition_during_acquisition_fails_closed():
    assert decide(
        market_session=MarketSession.CHANGED_DURING_ACQUISITION
    ) is BrokerStateDecision.UNKNOWN_BROKER_STATE


def test_unknown_session_fails_closed():
    assert decide(market_session=MarketSession.UNKNOWN) is (
        BrokerStateDecision.UNKNOWN_BROKER_STATE
    )


def test_availability_is_checked_before_anything_is_interpreted():
    """An unavailable read outranks a stale clock and a contradiction."""
    assert decide(
        order_read=ReadAvailability.UNAVAILABLE,
        evaluated_monotonic=9_999.0,
        position=PositionClassification.CONTRADICTORY,
    ) is BrokerStateDecision.BROKER_READ_UNAVAILABLE


# --- freshness policy -------------------------------------------------


def test_default_freshness_policy_is_conservative():
    policy = SnapshotFreshnessPolicy()
    assert policy.maximum_age_seconds <= 5.0
    assert policy.maximum_span_seconds <= policy.maximum_age_seconds


@pytest.mark.parametrize(
    "kwargs",
    [
        {"maximum_age_seconds": 0},
        {"maximum_age_seconds": -1},
        {"maximum_age_seconds": 61},
        {"maximum_span_seconds": 0},
        {"maximum_span_seconds": 61},
        {"maximum_age_seconds": 1.0, "maximum_span_seconds": 2.0},
        {"maximum_age_seconds": "5"},
        {"maximum_age_seconds": True},
    ],
)
def test_freshness_policy_rejects_unjustifiable_windows(kwargs):
    with pytest.raises(ReadOnlyGroundingError):
        SnapshotFreshnessPolicy(**kwargs)


def test_freshness_classification_is_explicit():
    assert snapshot().freshness() is SnapshotFreshness.FRESH
    assert snapshot(evaluated_monotonic=200.0).freshness() is (
        SnapshotFreshness.STALE
    )
    assert snapshot(last_read_monotonic=104.0, evaluated_monotonic=104.1
                    ).freshness() is SnapshotFreshness.SPAN_TOO_WIDE
    assert snapshot(first_read_monotonic=None).freshness() is (
        SnapshotFreshness.UNKNOWN
    )


# --- privacy ----------------------------------------------------------


def test_snapshot_has_no_field_capable_of_carrying_private_data():
    """The structural guarantee: no field can hold free-form broker text.

    Every field is a fixed enum, a bool, an int count, or a monotonic offset.
    There is nowhere to put an account number, an order identifier, a broker
    symbol, or a price even by mistake.
    """
    from enum import Enum

    permitted_scalars = (bool, int, float)
    for name, spec in ReadOnlyBrokerSnapshot.__dataclass_fields__.items():
        value = getattr(snapshot(), name)
        if value is None:
            continue
        assert isinstance(value, Enum) or isinstance(value, permitted_scalars), (
            name, type(value)
        )
        # A bare str field would be the only way private text could enter.
        assert not (isinstance(value, str) and not isinstance(value, Enum)), name


def test_snapshot_facts_are_all_allowlisted():
    facts = snapshot().sanitized_facts()
    validate_snapshot_facts(facts)
    for key, value in facts.items():
        assert value in ALLOWED_SNAPSHOT_FACTS[key]


@pytest.mark.parametrize(
    "facts",
    [
        {"account_number": "AVAILABLE"},
        {"order_id": "one"},
        {"account_read": "PRIVATE"},
        {"related_open_orders": "7"},
        {"account_read": 1},
        {"account_read": None},
    ],
)
def test_sanitizer_rejects_unknown_keys_and_values(facts):
    with pytest.raises(ReadOnlyGroundingError):
        validate_snapshot_facts(facts)


def test_sanitizer_rejects_a_non_mapping():
    for bad in ("facts", 7, None, ["account_read"]):
        with pytest.raises(ReadOnlyGroundingError):
            validate_snapshot_facts(bad)


def test_counts_are_bucketed_not_reported_exactly():
    assert count_bucket(0) == "none"
    assert count_bucket(1) == "one"
    assert count_bucket(2) == count_bucket(97) == "many"
    facts = snapshot(related_open_order_count=97).sanitized_facts()
    assert facts["related_open_orders"] == "many"
    assert "97" not in "".join(facts.values())


def test_assessment_facts_carry_the_decision_and_grounding_status():
    facts = decide_broker_state(snapshot()).facts
    assert facts["broker_state_decision"] == "VERIFIED_FLAT"
    assert facts["active_protection_grounding"] == "incomplete"
    validate_snapshot_facts(facts)


def test_negative_counts_are_rejected():
    for name in ("related_open_order_count", "executable_related_sell_count",
                 "protective_candidate_count"):
        with pytest.raises(ReadOnlyGroundingError):
            snapshot(**{name: -1})
        with pytest.raises(ReadOnlyGroundingError):
            snapshot(**{name: True})


# --- zero writes ------------------------------------------------------


def test_the_module_contains_no_write_call_site():
    """Asserted from the source tree, not from intent."""
    source = pathlib.Path("src/read_only_broker_state.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    for forbidden in ("place_equity_order", "cancel_equity_order",
                      "call_tool", "session", "post", "request"):
        assert forbidden not in called
    # No await, so nothing can reach an async broker adapter.
    assert not any(isinstance(node, ast.Await) for node in ast.walk(tree))


def test_the_decision_function_performs_no_io():
    """Checked against the parsed function, not its prose."""
    tree = ast.parse(inspect.getsource(decide_broker_state).strip())
    function = tree.body[0]

    assert not any(isinstance(node, ast.Await) for node in ast.walk(function))
    attribute_calls = {
        node.func.attr
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    plain_calls = {
        node.func.id
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    for forbidden in ("call_tool", "get_accounts", "get_equity_orders",
                      "get_equity_positions", "place_equity_order",
                      "cancel_equity_order", "connect", "send", "recv"):
        assert forbidden not in attribute_calls
        assert forbidden not in plain_calls
    for forbidden in ("open", "input", "eval", "exec", "__import__"):
        assert forbidden not in plain_calls


def test_prohibited_tools_are_named_and_excluded_from_the_plan():
    plan = ReadOnlyObservationPlan()
    assert set(plan.tools).isdisjoint(
        grounding.READ_ONLY_CHECKPOINT_PROHIBITED_TOOLS
    )
    assert plan.order_placements == 0
    assert plan.order_cancellations == 0
    assert plan.order_replacements == 0


def test_the_call_budget_is_per_tool_and_only_orders_gets_a_second_call():
    """The F2 budget: four calls, and the extra one is confined to orders.

    A uniform ceiling of 2 would have been simpler and wrong — it would grant
    `get_accounts` and `get_equity_positions` a second call nothing asked for.
    """
    plan = ReadOnlyObservationPlan()
    assert plan.call_ceilings["get_equity_orders"] == 2
    assert plan.call_ceilings["get_accounts"] == 1
    assert plan.call_ceilings["get_equity_positions"] == 1
    assert plan.maximum_total_calls == 4
    assert set(plan.call_ceilings) == set(plan.tools)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"order_placements": 1},
        {"order_cancellations": 1},
        {"order_replacements": 1},
        {"manufactures_observation": True},
        {"tools": ("get_accounts", "place_equity_order")},
        # A ceiling below one, a non-int ceiling, a ceiling for a tool that is
        # not planned, and a planned tool with no ceiling are all refusals.
        {"call_ceilings": {"get_accounts": 0, "get_equity_positions": 1,
                           "get_equity_orders": 2}},
        {"call_ceilings": {"get_accounts": True, "get_equity_positions": 1,
                           "get_equity_orders": 2}},
        {"call_ceilings": {"get_accounts": 1, "get_equity_positions": 1}},
        {"call_ceilings": {"get_accounts": 1, "get_equity_positions": 1,
                           "get_equity_orders": 2, "get_portfolio": 1}},
        {"call_ceilings": "two"},
    ],
)
def test_the_plan_refuses_any_write_shaped_configuration(kwargs):
    with pytest.raises(ReadOnlyGroundingError):
        ReadOnlyObservationPlan(**kwargs)


# --- observation promotion --------------------------------------------


def test_a_single_observation_never_reaches_approved():
    promoted = proposed_state_promotion(
        ObservationOutcome.OBSERVED_PENDING_REVIEW, "confirmed"
    )
    assert promoted is StateProvenance.READ_ONLY_LIVE_OBSERVATION
    assert promoted is not StateProvenance.APPROVED_FOR_ACTIVE_PROTECTION


def test_not_observed_is_a_legitimate_outcome_and_promotes_nothing():
    for outcome in (ObservationOutcome.NOT_OBSERVED,
                    ObservationOutcome.READ_UNAVAILABLE):
        with pytest.raises(ReadOnlyGroundingError):
            proposed_state_promotion(outcome, "confirmed")


def test_an_unknown_state_cannot_be_promoted():
    with pytest.raises(ReadOnlyGroundingError):
        proposed_state_promotion(
            ObservationOutcome.OBSERVED_PENDING_REVIEW, "not_a_broker_state"
        )


# --- the rest of the system is untouched ------------------------------


def test_decide_requires_a_real_snapshot():
    for bad in (None, {}, "snapshot", 7):
        with pytest.raises(ReadOnlyGroundingError):
            decide_broker_state(bad)


def test_strategy_remains_disabled_in_tracked_configuration():
    import re

    text = pathlib.Path("config/strategy_v0.yaml").read_text(encoding="utf-8")
    assert re.search(r"(?m)^\s*enabled:\s*false\s*$", text)


def test_grounding_does_not_import_any_broker_or_session_module():
    source = pathlib.Path("src/read_only_broker_state.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    for forbidden in ("mcp", "src.robinhood_client",
                      "src.robinhood_commissioning_session",
                      "src.robinhood_commissioning_adapter",
                      "src.robinhood_live_adapter"):
        assert forbidden not in imported
