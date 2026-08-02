"""Offline order lifecycle model — states, transitions, chains, reconciliation.

Every test is local. Nothing opens a session, reaches a broker, touches the
network, or imports a digest-pinned checkpoint file.

The properties that matter most:

1. **No lifecycle state is ever protective** — `WORKING` least of all, because
   its name is the most inviting.
2. `REPLACED` ends an identifier's life and continues only through a distinct
   successor.
3. Nothing here can permit automated action, and nothing can reach
   `ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES`.
"""

from __future__ import annotations

import ast
import itertools
import pathlib

import pytest

import src.order_lifecycle_model as lifecycle
from src.live_order_state_model import ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES
from src.order_lifecycle_model import (
    ABSENT_STATES,
    ALLOWED_TRANSITIONS,
    BACKWARD_TRANSITIONS,
    BENIGN_OUTCOMES,
    LABEL_MAPPINGS,
    LIFECYCLE_STATES_APPROVED_FOR_PROTECTION,
    MAXIMUM_REPLACEMENT_DEPTH,
    PROTECTION_EXPLANATION,
    PROVENANCE_SUFFICIENT_FOR_PROTECTION,
    REPLACEABLE_STATES,
    TERMINAL_STATES,
    ChainOutcome,
    ChainResult,
    LifecycleState,
    MappingProvenance,
    OrderLifecycleError,
    OrderObservation,
    ProtectionVerdict,
    ProtectionVerdictReason,
    ReconciliationOutcome,
    ReconciliationResult,
    assess_protection,
    label_provenance,
    normalize_label,
    reconcile,
    resolve_replacement_chain,
    state_provenance,
    transition_is_allowed,
    transition_is_backward,
)

MODULE_PATH = pathlib.Path(lifecycle.__file__)

PINNED_MODULES = (
    "read_only_observation_runtime",
    "read_only_broker_state",
    "read_only_authorization_manifest",
    "live_order_state_model",
)

S = LifecycleState

#: Independent expectation of the ten canonical states, written as literals.
EXPECTED_STATES = {
    "SUBMITTED", "ACCEPTED", "WORKING", "PARTIALLY_FILLED", "FILLED",
    "CANCELED", "REJECTED", "EXPIRED", "REPLACED", "UNKNOWN",
}


def obs(identifier, state, *, filled=0, at=0.0, replaces=None):
    return OrderObservation(
        identifier=identifier,
        state=state,
        cumulative_filled=filled,
        observed_monotonic=at,
        replaces_identifier=replaces,
    )


# --------------------------------------------------------------------------
# 1. States.
# --------------------------------------------------------------------------


def test_exactly_the_ten_required_states_exist():
    assert {state.value for state in LifecycleState} == EXPECTED_STATES
    assert len(list(LifecycleState)) == 10


def test_terminal_states_are_the_expected_five():
    assert {state.value for state in TERMINAL_STATES} == {
        "FILLED", "CANCELED", "REJECTED", "EXPIRED", "REPLACED",
    }


def test_partially_filled_is_not_terminal():
    assert S.PARTIALLY_FILLED not in TERMINAL_STATES


def test_unknown_is_not_terminal():
    """Unknown is a hole in knowledge, not an ending."""
    assert S.UNKNOWN not in TERMINAL_STATES


# --------------------------------------------------------------------------
# 2. Provenance.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "state,expected",
    [
        (S.FILLED, MappingProvenance.OBSERVED_LIVE),
        (S.CANCELED, MappingProvenance.OBSERVED_LIVE),
        (S.SUBMITTED, MappingProvenance.INFERRED_UNPROVEN),
        (S.ACCEPTED, MappingProvenance.INFERRED_UNPROVEN),
        (S.WORKING, MappingProvenance.INFERRED_UNPROVEN),
        (S.PARTIALLY_FILLED, MappingProvenance.SCHEMA_ONLY),
        (S.REJECTED, MappingProvenance.SCHEMA_ONLY),
        (S.EXPIRED, MappingProvenance.ABSENT),
        (S.REPLACED, MappingProvenance.ABSENT),
        (S.UNKNOWN, MappingProvenance.ABSENT),
    ],
)
def test_state_to_provenance_mapping(state, expected):
    assert state_provenance(state) is expected


def test_replaced_and_expired_have_no_broker_label_at_all():
    assert ABSENT_STATES == {S.EXPIRED, S.REPLACED}
    for state in ABSENT_STATES:
        assert not any(m.state is state for m in LABEL_MAPPINGS.values())


def test_the_inferred_states_are_inferred_because_the_broker_never_names_them():
    """`confirmed` is a broker label; `WORKING` is our reading of it."""
    assert normalize_label("confirmed") is S.WORKING
    assert label_provenance("confirmed") is MappingProvenance.INFERRED_UNPROVEN
    assert normalize_label("unconfirmed") is S.SUBMITTED
    assert label_provenance("unconfirmed") is MappingProvenance.INFERRED_UNPROVEN


def test_only_labels_with_live_evidence_claim_observed_provenance():
    """Cross-checked against the sanitized-evidence registry, not asserted."""
    from src.read_only_broker_state import (
        PROTECTION_STATE_CANDIDATES,
        StateProvenance,
    )

    observed = {
        name
        for name, candidate in PROTECTION_STATE_CANDIDATES.items()
        if candidate.provenance is StateProvenance.HISTORICAL_SANITIZED_EVIDENCE
    }
    claiming = {
        label
        for label, mapping in LABEL_MAPPINGS.items()
        if mapping.provenance is MappingProvenance.OBSERVED_LIVE
    }
    assert claiming <= observed, "a mapping claims evidence it does not have"
    assert claiming == {"filled", "cancelled"}


def test_provenance_is_derived_not_restated():
    """A mapping change must move the state's provenance with it."""
    for state in LifecycleState:
        candidates = [
            m.provenance for m in LABEL_MAPPINGS.values() if m.state is state
        ]
        if candidates:
            assert state_provenance(state) in candidates
        else:
            assert state_provenance(state) is MappingProvenance.ABSENT


# --------------------------------------------------------------------------
# 3. Normalization.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("label", sorted(LABEL_MAPPINGS))
def test_every_known_label_normalizes_to_a_real_state(label):
    assert normalize_label(label) in set(LifecycleState)


@pytest.mark.parametrize(
    "label,expected",
    [
        ("filled", S.FILLED),
        ("cancelled", S.CANCELED),
        ("partially_filled", S.PARTIALLY_FILLED),
        ("partially_filled_rest_cancelled", S.CANCELED),
        ("rejected", S.REJECTED),
        ("failed", S.REJECTED),
        ("locate_failed", S.REJECTED),
        ("voided", S.REJECTED),
        ("new", S.SUBMITTED),
        ("queued", S.ACCEPTED),
        ("locating", S.ACCEPTED),
        ("pending_cancelled", S.WORKING),
    ],
)
def test_specific_label_mappings(label, expected):
    assert normalize_label(label) is expected


@pytest.mark.parametrize(
    "label", ["FILLED", "  filled  ", "Filled", "CANCELLED", " Confirmed "]
)
def test_labels_are_normalized_for_case_and_whitespace(label):
    assert normalize_label(label) in {S.FILLED, S.CANCELED, S.WORKING}


@pytest.mark.parametrize(
    "label",
    ["", "   ", "wibble", "workin", "expired", "replaced", "submitted",
     None, 42, True, [], {}, object()],
)
def test_an_unrecognized_label_becomes_unknown_never_a_guess(label):
    """Note `expired`, `replaced`, `submitted`: plausible words, no mapping."""
    assert normalize_label(label) is S.UNKNOWN
    assert label_provenance(label) is MappingProvenance.ABSENT


def test_the_advertised_schema_is_fully_covered():
    from src.live_order_state_model import ADVERTISED_ORDER_OUTPUT_STATES

    uncovered = set(ADVERTISED_ORDER_OUTPUT_STATES) - set(LABEL_MAPPINGS)
    assert uncovered == set(), uncovered


# --------------------------------------------------------------------------
# 4. Protection — always false.
# --------------------------------------------------------------------------


def test_the_approved_set_is_empty_and_computed():
    assert LIFECYCLE_STATES_APPROVED_FOR_PROTECTION == frozenset()
    assert PROVENANCE_SUFFICIENT_FOR_PROTECTION == frozenset()


@pytest.mark.parametrize("state", list(LifecycleState))
def test_no_lifecycle_state_is_ever_protective(state):
    verdict = assess_protection(state)
    assert verdict.is_protective is False


def test_working_returns_a_hard_false_and_does_not_raise():
    """The decision: answer plainly, do not refuse."""
    verdict = assess_protection(S.WORKING)
    assert verdict.is_protective is False
    assert verdict.reason is (
        ProtectionVerdictReason.LIFECYCLE_STATE_CANNOT_ESTABLISH_PROTECTION
    )
    assert "read-only grounding checkpoint" in verdict.explanation


def test_the_explanation_names_what_would_be_required():
    for word in ("armed", "active", "venue-confirmed", "protective"):
        assert word in PROTECTION_EXPLANATION
    assert "grounding checkpoint" in PROTECTION_EXPLANATION


def test_replaced_is_never_protective_and_says_why():
    verdict = assess_protection(S.REPLACED)
    assert verdict.is_protective is False
    assert verdict.reason is ProtectionVerdictReason.ORDER_WAS_REPLACED


@pytest.mark.parametrize("state", sorted(TERMINAL_STATES - {S.REPLACED}, key=str))
def test_terminal_states_are_not_protective(state):
    verdict = assess_protection(state)
    assert verdict.is_protective is False
    assert verdict.reason is ProtectionVerdictReason.ORDER_IS_TERMINAL


def test_unknown_is_not_protective():
    assert assess_protection(S.UNKNOWN).reason is (
        ProtectionVerdictReason.STATE_IS_UNKNOWN
    )


def test_a_protective_verdict_cannot_be_constructed():
    """Structural: not a convention the callers must remember."""
    with pytest.raises(OrderLifecycleError):
        ProtectionVerdict(
            True, ProtectionVerdictReason.LIFECYCLE_STATE_CANNOT_ESTABLISH_PROTECTION
        )


def test_assess_protection_rejects_a_non_state():
    for value in ("WORKING", None, 3):
        with pytest.raises(OrderLifecycleError):
            assess_protection(value)


def test_this_module_cannot_reach_the_adapter_protection_mapping():
    before = frozenset(ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES)
    for state in LifecycleState:
        assess_protection(state)
    assert frozenset(ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES) == before
    assert before == frozenset()

    # Structural, not textual: the name appears in the module docstring, which
    # explains why the adapter mapping is empty. Prose about a symbol is not a
    # reference to it, so this checks the AST for an actual import or use.
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    referenced = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert "ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES" not in referenced


# --------------------------------------------------------------------------
# 5. Transitions.
# --------------------------------------------------------------------------


def test_every_state_has_a_transition_entry():
    assert set(ALLOWED_TRANSITIONS) == set(LifecycleState)


@pytest.mark.parametrize("state", sorted(TERMINAL_STATES, key=str))
def test_no_transition_leaves_a_terminal_state(state):
    assert ALLOWED_TRANSITIONS[state] == frozenset()
    for target in LifecycleState:
        assert transition_is_allowed(state, target) is False


ALLOWED_PAIRS = [
    (S.SUBMITTED, S.ACCEPTED), (S.SUBMITTED, S.REJECTED),
    (S.SUBMITTED, S.CANCELED), (S.SUBMITTED, S.EXPIRED),
    (S.ACCEPTED, S.WORKING), (S.ACCEPTED, S.PARTIALLY_FILLED),
    (S.ACCEPTED, S.FILLED), (S.ACCEPTED, S.CANCELED),
    (S.ACCEPTED, S.REJECTED), (S.ACCEPTED, S.EXPIRED), (S.ACCEPTED, S.REPLACED),
    (S.WORKING, S.PARTIALLY_FILLED), (S.WORKING, S.FILLED),
    (S.WORKING, S.CANCELED), (S.WORKING, S.EXPIRED), (S.WORKING, S.REPLACED),
    (S.PARTIALLY_FILLED, S.PARTIALLY_FILLED), (S.PARTIALLY_FILLED, S.FILLED),
    (S.PARTIALLY_FILLED, S.CANCELED), (S.PARTIALLY_FILLED, S.EXPIRED),
    (S.PARTIALLY_FILLED, S.REPLACED),
]


@pytest.mark.parametrize("previous,current", ALLOWED_PAIRS, ids=str)
def test_allowed_transitions(previous, current):
    assert transition_is_allowed(previous, current) is True


DISALLOWED_PAIRS = [
    (S.SUBMITTED, S.WORKING), (S.SUBMITTED, S.PARTIALLY_FILLED),
    (S.SUBMITTED, S.FILLED), (S.SUBMITTED, S.REPLACED),
    (S.ACCEPTED, S.SUBMITTED), (S.WORKING, S.ACCEPTED),
    (S.WORKING, S.SUBMITTED), (S.WORKING, S.REJECTED),
    (S.PARTIALLY_FILLED, S.WORKING), (S.PARTIALLY_FILLED, S.REJECTED),
    (S.FILLED, S.CANCELED), (S.CANCELED, S.FILLED),
    (S.REJECTED, S.WORKING), (S.EXPIRED, S.FILLED), (S.REPLACED, S.WORKING),
]


@pytest.mark.parametrize("previous,current", DISALLOWED_PAIRS, ids=str)
def test_disallowed_transitions(previous, current):
    assert transition_is_allowed(previous, current) is False


@pytest.mark.parametrize("pair", sorted(BACKWARD_TRANSITIONS, key=str), ids=str)
def test_every_backward_transition_is_flagged_and_disallowed(pair):
    previous, current = pair
    assert transition_is_backward(previous, current) is True
    assert transition_is_allowed(previous, current) is False


def test_backward_transitions_are_not_symmetric():
    """The forward direction of each backward pair must remain allowed."""
    for previous, current in BACKWARD_TRANSITIONS:
        assert transition_is_backward(current, previous) is False


def test_unknown_can_be_resolved_to_anything():
    for target in LifecycleState:
        assert transition_is_allowed(S.UNKNOWN, target) is True


def test_transition_check_rejects_non_states():
    with pytest.raises(OrderLifecycleError):
        transition_is_allowed("WORKING", S.FILLED)
    with pytest.raises(OrderLifecycleError):
        transition_is_allowed(S.WORKING, None)


def test_no_transition_pair_is_both_allowed_and_backward():
    for previous, current in itertools.product(LifecycleState, repeat=2):
        if transition_is_backward(previous, current):
            assert not transition_is_allowed(previous, current), (previous, current)


# --------------------------------------------------------------------------
# 6. Observations.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("identifier", ["", "   ", None, 42, []])
def test_a_missing_identifier_is_refused(identifier):
    with pytest.raises(OrderLifecycleError):
        obs(identifier, S.WORKING)


def test_a_non_state_is_refused():
    with pytest.raises(OrderLifecycleError):
        obs("A", "WORKING")


@pytest.mark.parametrize("filled", [-1, 1.5, True, "1", None])
def test_an_invalid_cumulative_fill_is_refused(filled):
    with pytest.raises(OrderLifecycleError):
        obs("A", S.PARTIALLY_FILLED, filled=filled)


def test_an_order_cannot_replace_itself():
    with pytest.raises(OrderLifecycleError):
        obs("A", S.REPLACED, replaces="A")


@pytest.mark.parametrize("parent", ["", "  ", 7])
def test_an_invalid_replacement_parent_is_refused(parent):
    with pytest.raises(OrderLifecycleError):
        obs("B", S.WORKING, replaces=parent)


# --------------------------------------------------------------------------
# 7. Reconciliation.
# --------------------------------------------------------------------------


def test_no_change_is_reported_as_such():
    result = reconcile(obs("A", S.WORKING, at=1.0), obs("A", S.WORKING, at=1.5))
    assert result.outcome is ReconciliationOutcome.NO_CHANGE
    assert result.fill_occurred is False


def test_forward_progress_is_consistent():
    result = reconcile(obs("A", S.WORKING, at=1.0), obs("A", S.FILLED, filled=1, at=1.5))
    assert result.outcome is ReconciliationOutcome.CONSISTENT_PROGRESS
    assert result.state is S.FILLED
    assert result.fill_occurred is True


def test_a_mismatched_identifier_is_refused():
    result = reconcile(obs("A", S.WORKING, at=1.0), obs("B", S.FILLED, at=1.5))
    assert result.outcome is ReconciliationOutcome.MISSING_IDENTIFIER
    assert result.state is S.UNKNOWN


def test_a_backward_observation_is_stale():
    result = reconcile(obs("A", S.WORKING, at=5.0), obs("A", S.FILLED, at=1.0))
    assert result.outcome is ReconciliationOutcome.STALE_OBSERVATION


def test_an_observation_beyond_the_age_bound_is_stale():
    result = reconcile(obs("A", S.WORKING, at=1.0), obs("A", S.FILLED, at=99.0))
    assert result.outcome is ReconciliationOutcome.STALE_OBSERVATION


def test_a_quantity_regression_is_refused():
    """Fills do not un-fill."""
    result = reconcile(
        obs("A", S.PARTIALLY_FILLED, filled=5, at=1.0),
        obs("A", S.PARTIALLY_FILLED, filled=2, at=1.5),
    )
    assert result.outcome is ReconciliationOutcome.QUANTITY_REGRESSION


def test_conflicting_terminal_states_are_refused():
    result = reconcile(obs("A", S.FILLED, at=1.0), obs("A", S.CANCELED, at=1.5))
    assert result.outcome is ReconciliationOutcome.CONFLICTING_TERMINAL_STATES
    assert result.state is S.UNKNOWN


def test_the_same_terminal_state_twice_is_not_a_conflict():
    result = reconcile(obs("A", S.FILLED, at=1.0), obs("A", S.FILLED, at=1.5))
    assert result.outcome is ReconciliationOutcome.NO_CHANGE


def test_an_impossible_transition_is_refused():
    result = reconcile(obs("A", S.SUBMITTED, at=1.0), obs("A", S.FILLED, at=1.5))
    assert result.outcome is ReconciliationOutcome.IMPOSSIBLE_TRANSITION


def test_a_backward_transition_is_refused():
    result = reconcile(obs("A", S.WORKING, at=1.0), obs("A", S.ACCEPTED, at=1.5))
    assert result.outcome is ReconciliationOutcome.BACKWARD_TRANSITION


def test_an_unknown_current_state_is_refused():
    result = reconcile(obs("A", S.WORKING, at=1.0), obs("A", S.UNKNOWN, at=1.5))
    assert result.outcome is ReconciliationOutcome.UNKNOWN_BROKER_STATE


def test_resolving_out_of_unknown_is_flagged():
    result = reconcile(obs("A", S.UNKNOWN, at=1.0), obs("A", S.FILLED, filled=1, at=1.5))
    assert result.outcome is ReconciliationOutcome.CONSISTENT_PROGRESS
    assert result.resolved_from_unknown is True


def test_unknown_to_unknown_stays_unknown():
    result = reconcile(obs("A", S.UNKNOWN, at=1.0), obs("A", S.UNKNOWN, at=1.5))
    assert result.outcome is ReconciliationOutcome.UNKNOWN_BROKER_STATE


def test_a_partial_fill_progressing_is_consistent():
    result = reconcile(
        obs("A", S.PARTIALLY_FILLED, filled=1, at=1.0),
        obs("A", S.PARTIALLY_FILLED, filled=3, at=1.5),
    )
    assert result.outcome is ReconciliationOutcome.CONSISTENT_PROGRESS
    assert result.fill_occurred is True
    assert result.state is S.PARTIALLY_FILLED


def test_cancellation_after_a_partial_fill_preserves_that_some_filled():
    """`partially_filled_rest_cancelled` must not erase the executed quantity."""
    assert normalize_label("partially_filled_rest_cancelled") is S.CANCELED
    result = reconcile(
        obs("A", S.PARTIALLY_FILLED, filled=2, at=1.0),
        obs("A", S.CANCELED, filled=2, at=1.5),
    )
    # Pinned to the single correct outcome. A disjunctive assertion here would
    # accept a behaviour change in either direction without noticing.
    assert result.outcome is ReconciliationOutcome.CONSISTENT_PROGRESS
    assert result.fill_occurred is False   # no *additional* fill in this step
    assert result.state is S.CANCELED
    # The fill is still represented in the observation that produced it.
    assert obs("A", S.CANCELED, filled=2).cumulative_filled == 2


def test_reconcile_rejects_non_observations():
    with pytest.raises(OrderLifecycleError):
        reconcile(obs("A", S.WORKING), "not an observation")


@pytest.mark.parametrize("outcome", list(ReconciliationOutcome))
def test_no_reconciliation_outcome_permits_action(outcome):
    result = ReconciliationResult(outcome, S.UNKNOWN)
    assert result.permits_automated_action is False


def test_a_result_permitting_action_cannot_be_constructed():
    with pytest.raises(OrderLifecycleError):
        ReconciliationResult(
            ReconciliationOutcome.CONSISTENT_PROGRESS, S.FILLED,
            permits_automated_action=True,
        )


def test_only_two_outcomes_are_benign_and_neither_permits_action():
    assert BENIGN_OUTCOMES == {
        ReconciliationOutcome.CONSISTENT_PROGRESS,
        ReconciliationOutcome.NO_CHANGE,
    }
    for outcome in BENIGN_OUTCOMES:
        assert ReconciliationResult(outcome, S.FILLED).permits_automated_action is False


# --------------------------------------------------------------------------
# 8. Replacement chains.
# --------------------------------------------------------------------------


def test_an_order_with_no_successor_reports_no_replacement():
    result = resolve_replacement_chain({"A": obs("A", S.WORKING)}, "A")
    assert result.outcome is ChainOutcome.NO_REPLACEMENT
    assert result.depth == 0


def test_a_simple_chain_resolves_to_the_successor():
    observations = {
        "A": obs("A", S.REPLACED),
        "B": obs("B", S.WORKING, replaces="A"),
    }
    result = resolve_replacement_chain(observations, "A")
    assert result.outcome is ChainOutcome.RESOLVED
    assert result.depth == 1
    assert result.terminal_state is S.WORKING


def test_a_chain_continues_only_through_a_distinct_identifier():
    observations = {
        "A": obs("A", S.REPLACED),
        "B": obs("B", S.REPLACED, replaces="A"),
        "C": obs("C", S.FILLED, replaces="B"),
    }
    result = resolve_replacement_chain(observations, "A")
    assert result.outcome is ChainOutcome.RESOLVED
    assert result.depth == 2
    assert result.terminal_state is S.FILLED


def test_a_missing_root_is_refused():
    assert resolve_replacement_chain({}, "A").outcome is ChainOutcome.MISSING_PARENT


def test_a_missing_parent_is_refused():
    observations = {"B": obs("B", S.WORKING, replaces="A")}
    assert (
        resolve_replacement_chain(observations, "B").outcome
        is ChainOutcome.MISSING_PARENT
    )


def test_an_unrelated_orphan_does_not_poison_a_valid_chain():
    """Review finding: a dangling parent elsewhere used to fail this chain.

    Reporting MISSING_PARENT here would make a valid result depend on
    unrelated data, and would hide a real chain problem behind someone
    else's.
    """
    observations = {
        "A": obs("A", S.REPLACED),
        "B": obs("B", S.WORKING, replaces="A"),
        "Z": obs("Z", S.WORKING, replaces="GHOST"),   # unrelated, orphaned
    }
    result = resolve_replacement_chain(observations, "A")
    assert result.outcome is ChainOutcome.RESOLVED
    assert result.depth == 1
    assert result.terminal_state is S.WORKING


def test_a_dangling_parent_on_this_chain_is_still_refused():
    """The narrowing must not become a loophole."""
    observations = {
        "A": obs("A", S.REPLACED, replaces="GHOST"),
        "B": obs("B", S.WORKING, replaces="A"),
    }
    assert (
        resolve_replacement_chain(observations, "A").outcome
        is ChainOutcome.MISSING_PARENT
    )


def test_a_dangling_parent_reached_mid_walk_is_refused():
    observations = {
        "A": obs("A", S.REPLACED),
        "B": obs("B", S.REPLACED, replaces="A"),
        "C": obs("C", S.WORKING, replaces="B"),
    }
    # Corrupt B's history: it now claims a parent nobody holds.
    observations["B"] = obs("B", S.REPLACED, replaces="GHOST")
    result = resolve_replacement_chain(observations, "A")
    assert result.outcome in {ChainOutcome.MISSING_PARENT, ChainOutcome.NO_REPLACEMENT}


def test_a_duplicate_child_anywhere_still_fails_globally():
    """Unlike a dangling parent, a duplicate means correlation is unreliable."""
    observations = {
        "A": obs("A", S.REPLACED),
        "B": obs("B", S.WORKING, replaces="A"),
        "P": obs("P", S.REPLACED),
        "Q": obs("Q", S.WORKING, replaces="P"),
        "R": obs("R", S.WORKING, replaces="P"),   # unrelated duplicate
    }
    assert (
        resolve_replacement_chain(observations, "A").outcome
        is ChainOutcome.DUPLICATE_CHILD
    )


def test_two_children_of_one_parent_are_ambiguous():
    observations = {
        "A": obs("A", S.REPLACED),
        "B": obs("B", S.WORKING, replaces="A"),
        "C": obs("C", S.WORKING, replaces="A"),
    }
    assert (
        resolve_replacement_chain(observations, "A").outcome
        is ChainOutcome.DUPLICATE_CHILD
    )


def test_a_cycle_is_detected():
    observations = {
        "A": obs("A", S.REPLACED, replaces="B"),
        "B": obs("B", S.REPLACED, replaces="A"),
    }
    result = resolve_replacement_chain(observations, "A")
    assert result.outcome is ChainOutcome.CYCLE_DETECTED


def test_an_excessively_deep_chain_is_refused():
    observations = {"O0": obs("O0", S.REPLACED)}
    for index in range(1, MAXIMUM_REPLACEMENT_DEPTH + 2):
        observations[f"O{index}"] = obs(
            f"O{index}", S.REPLACED, replaces=f"O{index - 1}"
        )
    result = resolve_replacement_chain(observations, "O0")
    assert result.outcome is ChainOutcome.EXCESSIVE_DEPTH


def test_a_chain_at_the_depth_bound_still_resolves():
    observations = {"O0": obs("O0", S.REPLACED)}
    for index in range(1, MAXIMUM_REPLACEMENT_DEPTH):
        observations[f"O{index}"] = obs(
            f"O{index}", S.REPLACED, replaces=f"O{index - 1}"
        )
    observations[f"O{MAXIMUM_REPLACEMENT_DEPTH}"] = obs(
        f"O{MAXIMUM_REPLACEMENT_DEPTH}", S.WORKING,
        replaces=f"O{MAXIMUM_REPLACEMENT_DEPTH - 1}",
    )
    result = resolve_replacement_chain(observations, "O0")
    assert result.outcome is ChainOutcome.RESOLVED
    assert result.depth == MAXIMUM_REPLACEMENT_DEPTH


@pytest.mark.parametrize(
    "parent_state", [S.FILLED, S.CANCELED, S.REJECTED, S.EXPIRED, S.SUBMITTED, S.UNKNOWN]
)
def test_an_invalid_parent_state_is_refused(parent_state):
    """A finished order cannot be superseded; it has already ended."""
    observations = {
        "A": obs("A", parent_state),
        "B": obs("B", S.WORKING, replaces="A"),
    }
    assert (
        resolve_replacement_chain(observations, "A").outcome
        is ChainOutcome.INVALID_PARENT_STATE
    )


@pytest.mark.parametrize("parent_state", sorted(REPLACEABLE_STATES, key=str))
def test_replaceable_parent_states(parent_state):
    observations = {
        "A": obs("A", parent_state),
        "B": obs("B", S.WORKING, replaces="A"),
    }
    assert resolve_replacement_chain(observations, "A").outcome is ChainOutcome.RESOLVED


def test_chain_resolution_rejects_bad_arguments():
    with pytest.raises(OrderLifecycleError):
        resolve_replacement_chain([], "A")
    with pytest.raises(OrderLifecycleError):
        resolve_replacement_chain({}, "")


def test_no_chain_result_permits_action():
    for outcome in ChainOutcome:
        assert ChainResult(outcome).permits_automated_action is False
    with pytest.raises(OrderLifecycleError):
        ChainResult(ChainOutcome.RESOLVED, permits_automated_action=True)


# --------------------------------------------------------------------------
# 9. Privacy.
# --------------------------------------------------------------------------


#: Every value a sanitized reconciliation fact may take. Written out rather
#: than derived, so a new value cannot slip in by widening an enum.
ALLOWED_FACT_VALUES = {
    "reconciliation_outcome": {outcome.value for outcome in ReconciliationOutcome},
    "lifecycle_state": {state.value for state in LifecycleState},
    "state_provenance": {p.value for p in MappingProvenance},
    "is_terminal": {"true", "false"},
    "resolved_from_unknown": {"true", "false"},
    "fill_occurred": {"true", "false"},
    "permits_automated_action": {"false"},
}


def test_reconciliation_facts_carry_no_identifier_quantity_or_payload():
    """Allowlisted, not spot-checked.

    Asserting that the digits 7 and 9 are absent would pass by coincidence —
    the facts contain no digits at all — and would say nothing about a value
    that leaked next year. Every key and value is checked against a fixed
    vocabulary instead.
    """
    result = reconcile(
        obs("SECRET-ORDER-123", S.PARTIALLY_FILLED, filled=7, at=1.0),
        obs("SECRET-ORDER-123", S.FILLED, filled=9, at=1.5),
    )
    facts = result.sanitized_facts()
    assert set(facts) == set(ALLOWED_FACT_VALUES)
    for key, value in facts.items():
        assert value in ALLOWED_FACT_VALUES[key], (key, value)
    assert facts["fill_occurred"] == "true"
    assert facts["permits_automated_action"] == "false"


def test_no_reconciliation_fact_value_escapes_the_allowlist():
    """Drive many shapes through and check every emitted value."""
    cases = [
        (obs("A", S.WORKING, at=1.0), obs("A", S.FILLED, filled=1, at=1.5)),
        (obs("A", S.UNKNOWN, at=1.0), obs("A", S.WORKING, at=1.5)),
        (obs("A", S.FILLED, at=1.0), obs("A", S.CANCELED, at=1.5)),
        (obs("A", S.WORKING, at=5.0), obs("A", S.FILLED, at=1.0)),
        (obs("A", S.PARTIALLY_FILLED, filled=9, at=1.0),
         obs("A", S.PARTIALLY_FILLED, filled=2, at=1.5)),
        (obs("A", S.WORKING, at=1.0), obs("B", S.FILLED, at=1.5)),
    ]
    for previous, current in cases:
        facts = reconcile(previous, current).sanitized_facts()
        assert set(facts) == set(ALLOWED_FACT_VALUES)
        for key, value in facts.items():
            assert value in ALLOWED_FACT_VALUES[key], (key, value)


def test_chain_facts_carry_no_identifier_and_bucket_the_depth():
    observations = {
        "PARENT-XYZ": obs("PARENT-XYZ", S.REPLACED),
        "CHILD-XYZ": obs("CHILD-XYZ", S.WORKING, replaces="PARENT-XYZ"),
    }
    facts = resolve_replacement_chain(observations, "PARENT-XYZ").sanitized_facts()
    rendered = repr(facts)
    assert "PARENT-XYZ" not in rendered and "CHILD-XYZ" not in rendered
    assert facts["chain_depth_bucket"] in {"none", "one", "many"}


def test_sanitized_facts_survive_the_commissioning_privacy_sanitizer():
    from src.one_share_commissioning import validate_sanitized_evidence

    result = reconcile(obs("A", S.WORKING, at=1.0), obs("A", S.FILLED, filled=1, at=1.5))
    validate_sanitized_evidence(result.sanitized_facts())
    observations = {"A": obs("A", S.REPLACED), "B": obs("B", S.WORKING, replaces="A")}
    validate_sanitized_evidence(
        resolve_replacement_chain(observations, "A").sanitized_facts()
    )


def test_every_sanitized_value_is_a_fixed_string():
    result = reconcile(obs("A", S.WORKING, at=1.0), obs("A", S.FILLED, at=1.5))
    for key, value in result.sanitized_facts().items():
        assert isinstance(key, str) and isinstance(value, str)


def test_no_dataclass_field_can_carry_a_price_or_symbol():
    import dataclasses

    for cls in (ReconciliationResult, ChainResult, ProtectionVerdict):
        names = {f.name for f in dataclasses.fields(cls)}
        for forbidden in ("price", "symbol", "quantity", "identifier", "payload"):
            assert not any(forbidden in name for name in names), (cls, forbidden)


# --------------------------------------------------------------------------
# 10. Independence.
# --------------------------------------------------------------------------


def _imports(path: pathlib.Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_the_model_imports_no_pinned_checkpoint_file():
    imported = _imports(MODULE_PATH)
    for pinned in PINNED_MODULES:
        assert not any(pinned in name for name in imported), pinned


def test_the_model_imports_nothing_broker_shaped():
    forbidden = ("adapter", "client", "session", "mcp", "orchestrator",
                 "commissioning", "manifest", "checkpoint", "strategy", "broker")
    for name in _imports(MODULE_PATH):
        assert not any(word in name.lower() for word in forbidden), name


def test_the_model_makes_no_network_call_and_performs_no_io():
    imported = _imports(MODULE_PATH)
    assert imported.isdisjoint(
        {"socket", "http", "urllib", "requests", "httpx", "aiohttp", "subprocess", "os"}
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


def test_the_model_reads_no_wall_clock():
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "datetime.now(" not in source
    assert "time.time(" not in source


def test_the_model_defines_no_write_shaped_function():
    """Matched on whole name tokens, not substrings.

    A substring rule flags `resolve_replacement_chain` for containing
    "place" — which is how a guard earns a reputation for crying wolf and
    then gets weakened. Splitting on underscores keeps it honest.
    """
    forbidden = {"place", "cancel", "submit", "activate", "execute", "send"}
    for node in ast.walk(ast.parse(MODULE_PATH.read_text(encoding="utf-8"))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            tokens = set(node.name.lower().strip("_").split("_"))
            assert not (tokens & forbidden), node.name


def test_permits_automated_action_is_false_everywhere_in_the_source():
    """No literal `True` is ever assigned to the flag."""
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "permits_automated_action: bool = False" in source
    assert "permits_automated_action=True" not in source
