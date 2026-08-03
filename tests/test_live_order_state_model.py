"""
Offline tests for the conservative equity-order state classification model.

No broker access occurs. Every fixture value is INVENTED.

The central property under test: a working order state NEVER establishes
protective coverage on its own. The schema advertises lifecycle names, not
which of them mean a stop is armed and resting at the venue.
"""
from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path

import pytest

from src.live_broker_capabilities import APPROVED_PROTECTION_INVARIANT
from src.live_order_contract import ADVERTISED_ORDER_OUTPUT_STATES
from src.live_order_state_model import (
    ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES,
    TERMINAL_ORDER_STATE_CLASSES,
    WORKING_ORDER_STATES,
    OrderStateClass,
    ProtectionEvidenceGap,
    ProtectiveCoverage,
    assess_protective_coverage,
    classify_order_state,
    is_advertised_state,
    lifecycle_projection,
    normalize_state,
)


REPOSITORY_ROOT = Path(__file__).parent.parent


def protective_stop(**overrides):
    """An otherwise-perfect protective stop over an invented position."""
    arguments = {
        "state": "queued",
        "side": "sell",
        "trigger": "stop",
        "stop_price": Decimal("10.25"),
        "stop_submitted_quantity": "4",
        "stop_cumulative_quantity": "0",
        "verified_remaining_long_quantity": "4",
    }
    arguments.update(overrides)
    return assess_protective_coverage(**arguments)


# ---------- the seven classes exist ----------

def test_every_required_class_exists():
    assert {member.value for member in OrderStateClass} == {
        "ACTIVE_OR_WORKING",
        "CANCELLATION_PENDING",
        "TERMINAL_CANCELLED",
        "TERMINAL_FILLED",
        "TERMINAL_REJECTED_OR_FAILED",
        "PARTIAL_FILL_WITH_REMAINDER_CANCELLED",
        "INDETERMINATE_OR_UNSUPPORTED",
    }


# ---------- the grounded mappings ----------

@pytest.mark.parametrize(
    "state, expected",
    [
        ("pending_cancelled", OrderStateClass.CANCELLATION_PENDING),
        ("cancelled", OrderStateClass.TERMINAL_CANCELLED),
        (
            "partially_filled_rest_cancelled",
            OrderStateClass.PARTIAL_FILL_WITH_REMAINDER_CANCELLED,
        ),
        ("filled", OrderStateClass.TERMINAL_FILLED),
        ("rejected", OrderStateClass.TERMINAL_REJECTED_OR_FAILED),
        ("failed", OrderStateClass.TERMINAL_REJECTED_OR_FAILED),
        ("voided", OrderStateClass.TERMINAL_REJECTED_OR_FAILED),
    ],
)
def test_grounded_mappings(state, expected):
    assert classify_order_state(state) is expected


@pytest.mark.parametrize(
    "state",
    ["new", "queued", "confirmed", "unconfirmed", "partially_filled",
     "locating"],
)
def test_working_states_are_active_or_working(state):
    assert classify_order_state(state) is OrderStateClass.ACTIVE_OR_WORKING
    assert state in WORKING_ORDER_STATES


def test_pending_cancelled_is_not_terminal():
    assert (
        classify_order_state("pending_cancelled")
        not in TERMINAL_ORDER_STATE_CLASSES
    )


def test_terminal_classes_are_exactly_the_four_terminal_ones():
    assert TERMINAL_ORDER_STATE_CLASSES == {
        OrderStateClass.TERMINAL_CANCELLED,
        OrderStateClass.TERMINAL_FILLED,
        OrderStateClass.TERMINAL_REJECTED_OR_FAILED,
        OrderStateClass.PARTIAL_FILL_WITH_REMAINDER_CANCELLED,
    }


@pytest.mark.parametrize("state", sorted(ADVERTISED_ORDER_OUTPUT_STATES))
def test_every_advertised_output_state_is_classified(state):
    assert (
        classify_order_state(state)
        is not OrderStateClass.INDETERMINATE_OR_UNSUPPORTED
    )
    assert is_advertised_state(state)


def test_case_and_whitespace_are_normalized():
    assert classify_order_state("  FILLED  ") is OrderStateClass.TERMINAL_FILLED
    assert normalize_state("  Queued ") == "queued"


# ---------- unknown states fail closed ----------

@pytest.mark.parametrize(
    "state",
    [
        None,
        "",
        "   ",
        7,
        True,
        [],
        {},
        "unknown_state",
        "open",
        "accepted",
        "expired",
        # Deliberately NOT aliased: an unadvertised spelling is unknown.
        "canceled",
        "pending_canceled",
        "partially_filled_rest_canceled",
    ],
)
def test_unrecognized_states_fail_closed(state):
    assert (
        classify_order_state(state)
        is OrderStateClass.INDETERMINATE_OR_UNSUPPORTED
    )


def test_unadvertised_spellings_are_not_silently_aliased():
    """'canceled' must not become an advertised terminal cancellation."""
    assert classify_order_state("canceled") is not (
        OrderStateClass.TERMINAL_CANCELLED
    )
    assert is_advertised_state("canceled") is False


# ---------- the protective-coverage assessment ----------

def test_the_three_coverage_values_exist():
    assert {member.value for member in ProtectiveCoverage} == {
        "VERIFIED_ACTIVE_PROTECTION",
        "NOT_PROTECTED",
        "INDETERMINATE",
    }


def test_the_grounded_permitted_state_set_is_empty():
    """
    Nothing grounds which working state means armed protection.

    Until a later adapter-initialization checkpoint grounds it, this set
    must stay empty.
    """
    assert ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES == frozenset()


@pytest.mark.parametrize("state", sorted(WORKING_ORDER_STATES))
def test_working_states_never_establish_protection_by_default(state):
    """An otherwise-perfect stop in a working state is INDETERMINATE."""
    assessment = protective_stop(state=state)

    assert assessment.coverage is ProtectiveCoverage.INDETERMINATE
    assert (
        ProtectionEvidenceGap.STATE_NOT_GROUNDED_AS_ACTIVE_PROTECTION
        in assessment.gaps
    )


def test_no_input_can_produce_verified_protection_with_the_grounded_set():
    """
    The default surface cannot return VERIFIED_ACTIVE_PROTECTION at all.

    Every advertised state is either working (ungrounded), pending
    cancellation, or terminal.
    """
    for state in sorted(ADVERTISED_ORDER_OUTPUT_STATES):
        assert protective_stop(state=state).coverage is not (
            ProtectiveCoverage.VERIFIED_ACTIVE_PROTECTION
        )


def test_a_future_grounded_state_can_verify_protection():
    """
    The mechanism works once a checkpoint grounds a state.

    This passes an explicit permitted set; it does NOT change the grounded
    default, which the previous test pins to empty.
    """
    assessment = protective_stop(
        state="queued", permitted_active_protection_states=frozenset({"queued"})
    )

    assert assessment.coverage is ProtectiveCoverage.VERIFIED_ACTIVE_PROTECTION
    assert assessment.gaps == ()
    assert assessment.is_verified is True


def test_pending_cancellation_is_never_protection():
    assessment = protective_stop(
        state="pending_cancelled",
        permitted_active_protection_states=frozenset(
            {"pending_cancelled", "queued"}
        ),
    )
    assert assessment.coverage is ProtectiveCoverage.NOT_PROTECTED
    assert ProtectionEvidenceGap.CANCELLATION_PENDING in assessment.gaps


@pytest.mark.parametrize(
    "state",
    ["cancelled", "filled", "rejected", "failed", "voided",
     "partially_filled_rest_cancelled", "locate_failed"],
)
def test_terminal_states_are_never_protection(state):
    assessment = protective_stop(
        state=state,
        permitted_active_protection_states=ADVERTISED_ORDER_OUTPUT_STATES,
    )
    assert assessment.coverage is ProtectiveCoverage.NOT_PROTECTED
    assert ProtectionEvidenceGap.TERMINAL_STATE in assessment.gaps


def test_unrecognized_state_is_indeterminate_not_protected():
    assessment = protective_stop(state="something_new")
    assert assessment.coverage is ProtectiveCoverage.INDETERMINATE
    assert ProtectionEvidenceGap.UNRECOGNIZED_STATE in assessment.gaps


# ---------- exact stop quantity ----------

GROUNDED = frozenset({"queued"})


@pytest.mark.parametrize(
    "stop_quantity, position",
    [
        ("3", "4"),   # undersized: leaves exposure
        ("5", "4"),   # oversized: risks an oversell
        ("4", "5"),
        ("1", "4"),
    ],
)
def test_exact_stop_quantity_is_required(stop_quantity, position):
    assessment = protective_stop(
        stop_submitted_quantity=stop_quantity,
        verified_remaining_long_quantity=position,
        permitted_active_protection_states=GROUNDED,
    )
    assert assessment.coverage is ProtectiveCoverage.NOT_PROTECTED
    assert ProtectionEvidenceGap.QUANTITY_DISAGREEMENT in assessment.gaps


def test_a_partially_filled_stop_no_longer_covers_the_position():
    """The stop's REMAINING quantity is what covers, not its submitted size."""
    assessment = protective_stop(
        stop_submitted_quantity="4",
        stop_cumulative_quantity="1",
        verified_remaining_long_quantity="4",
        permitted_active_protection_states=GROUNDED,
    )
    assert assessment.coverage is ProtectiveCoverage.NOT_PROTECTED
    assert ProtectionEvidenceGap.QUANTITY_DISAGREEMENT in assessment.gaps


def test_exact_remaining_coverage_after_a_partial_stop_fill_is_accepted():
    assessment = protective_stop(
        stop_submitted_quantity="4",
        stop_cumulative_quantity="1",
        verified_remaining_long_quantity="3",
        permitted_active_protection_states=GROUNDED,
    )
    assert assessment.coverage is ProtectiveCoverage.VERIFIED_ACTIVE_PROTECTION


def test_no_position_is_not_protection():
    assessment = protective_stop(
        stop_submitted_quantity="0",
        verified_remaining_long_quantity="0",
        permitted_active_protection_states=GROUNDED,
    )
    assert assessment.coverage is ProtectiveCoverage.NOT_PROTECTED
    assert ProtectionEvidenceGap.NO_POSITION_TO_PROTECT in assessment.gaps


@pytest.mark.parametrize(
    "quantity", ["abc", "", None, 1.5, -1, {}, [], True]
)
def test_malformed_quantities_are_indeterminate(quantity):
    assessment = protective_stop(
        stop_submitted_quantity=quantity,
        permitted_active_protection_states=GROUNDED,
    )
    assert assessment.coverage is ProtectiveCoverage.INDETERMINATE
    assert ProtectionEvidenceGap.MALFORMED_QUANTITY in assessment.gaps


@pytest.mark.parametrize("quantity", ["abc", 1.5, -1, None])
def test_malformed_position_quantities_are_indeterminate(quantity):
    assessment = protective_stop(
        verified_remaining_long_quantity=quantity,
        permitted_active_protection_states=GROUNDED,
    )
    assert assessment.coverage is ProtectiveCoverage.INDETERMINATE


# ---------- protective order shape ----------

@pytest.mark.parametrize("side", ["buy", "", None, "SELLL", 7])
def test_a_non_sell_order_is_not_protection(side):
    assessment = protective_stop(
        side=side, permitted_active_protection_states=GROUNDED
    )
    assert assessment.coverage is ProtectiveCoverage.NOT_PROTECTED
    assert ProtectionEvidenceGap.NOT_A_SELL_ORDER in assessment.gaps


@pytest.mark.parametrize("trigger", ["immediate", "", None, 7])
def test_a_missing_stop_trigger_is_not_protection(trigger):
    assessment = protective_stop(
        trigger=trigger, permitted_active_protection_states=GROUNDED
    )
    assert assessment.coverage is ProtectiveCoverage.NOT_PROTECTED
    assert ProtectionEvidenceGap.NO_STOP_TRIGGER in assessment.gaps


@pytest.mark.parametrize(
    "stop_price", [None, Decimal("0"), Decimal("-1.00")]
)
def test_a_missing_stop_price_is_not_protection(stop_price):
    assessment = protective_stop(
        stop_price=stop_price, permitted_active_protection_states=GROUNDED
    )
    assert assessment.coverage is ProtectiveCoverage.NOT_PROTECTED
    assert ProtectionEvidenceGap.NO_STOP_PRICE in assessment.gaps


def test_a_plain_working_sell_order_is_not_treated_as_a_stop():
    """An ordinary resting sell is not protective coverage."""
    assessment = protective_stop(
        trigger="immediate",
        stop_price=None,
        permitted_active_protection_states=GROUNDED,
    )
    assert assessment.coverage is ProtectiveCoverage.NOT_PROTECTED


# ---------- the assessment is a report, not a decision ----------

def test_the_approved_invariant_is_quoted_verbatim():
    assessment = protective_stop()
    assert assessment.invariant == APPROVED_PROTECTION_INVARIANT
    assert assessment.invariant == (
        "No filled position may exist without verified protective-stop "
        "coverage for its exact filled quantity."
    )


def test_the_assessment_is_immutable():
    assessment = protective_stop()
    with pytest.raises(FrozenInstanceError):
        assessment.coverage = (  # type: ignore[misc]
            ProtectiveCoverage.VERIFIED_ACTIVE_PROTECTION
        )


def test_gap_codes_are_plain_reason_strings():
    assessment = protective_stop(side="buy")
    assert all(isinstance(code, str) for code in assessment.gap_codes)
    assert "NOT_A_SELL_ORDER" in assessment.gap_codes


# ---------- static safety ----------

def test_module_implements_no_order_submission_or_exit():
    source = (
        REPOSITORY_ROOT / "src" / "live_order_state_model.py"
    ).read_text(encoding="utf-8").lower()

    for forbidden in (
        "call_tool",
        "def place",
        "def submit",
        "def cancel",
        "def exit",
        "def retry",
        "max_retries",
        "time.sleep",
        "asyncio.sleep",
    ):
        assert forbidden not in source


def test_module_has_no_async_entry_point():
    tree = ast.parse(
        (REPOSITORY_ROOT / "src" / "live_order_state_model.py").read_text(
            encoding="utf-8"
        )
    )
    for node in ast.walk(tree):
        assert not isinstance(node, ast.AsyncFunctionDef)
        assert not isinstance(node, ast.Await)


def lifecycle_projection_for_state(state):
    """Project directly from a canonical state, for states no label produces."""
    from src.live_order_state_model import (
        LIFECYCLE_REFUSAL_REASONS,
        LifecycleProjection,
    )
    from src.order_lifecycle_model import (
        MappingProvenance,
        assess_protection,
        state_provenance,
    )

    verdict = assess_protection(state)
    return LifecycleProjection(
        lifecycle_state=state,
        provenance=state_provenance(state) or MappingProvenance.ABSENT,
        cannot_protect=verdict.reason in LIFECYCLE_REFUSAL_REASONS,
    )


# --------------------------------------------------------------------------
# C: the canonical lifecycle model may veto, and may never grant.
# --------------------------------------------------------------------------


def test_working_is_never_protective():
    """The state the whole adoption is careful about."""
    from src.order_lifecycle_model import LifecycleState

    projection = lifecycle_projection("confirmed")
    assert projection.lifecycle_state is LifecycleState.WORKING
    assert projection.is_protective is False
    assert projection.permits_automated_action is False
    # But WORKING is NOT vetoed: a future grounded approval must remain able to
    # name it, or step 5 of the milestone becomes unreachable.
    assert projection.cannot_protect is False


def test_replaced_is_never_protective_and_is_vetoed():
    from src.order_lifecycle_model import LifecycleState

    projection = lifecycle_projection_for_state(LifecycleState.REPLACED)
    assert projection.is_protective is False
    assert projection.cannot_protect is True


def test_an_unknown_label_stays_unknown_and_is_vetoed():
    from src.order_lifecycle_model import LifecycleState, MappingProvenance

    projection = lifecycle_projection("wibble")
    assert projection.lifecycle_state is LifecycleState.UNKNOWN
    assert projection.provenance is MappingProvenance.ABSENT
    assert projection.cannot_protect is True
    assert projection.is_protective is False


@pytest.mark.parametrize("label", ["filled", "cancelled", "rejected", "voided"])
def test_terminal_labels_are_vetoed(label):
    assert lifecycle_projection(label).cannot_protect is True


def test_a_projection_can_never_assert_protection_or_action():
    from src.live_order_state_model import LifecycleProjection
    from src.order_lifecycle_model import LifecycleState, MappingProvenance

    with pytest.raises(ValueError):
        LifecycleProjection(
            lifecycle_state=LifecycleState.WORKING,
            provenance=MappingProvenance.INFERRED_UNPROVEN,
            cannot_protect=False,
            is_protective=True,
        )
    with pytest.raises(ValueError):
        LifecycleProjection(
            lifecycle_state=LifecycleState.WORKING,
            provenance=MappingProvenance.INFERRED_UNPROVEN,
            cannot_protect=False,
            permits_automated_action=True,
        )


def test_projection_facts_carry_no_identifier_or_value():
    facts = lifecycle_projection("confirmed").sanitized_facts()
    assert set(facts) == {
        "lifecycle_state",
        "lifecycle_provenance",
        "lifecycle_cannot_protect",
        "permits_automated_action",
    }
    assert facts["permits_automated_action"] == "false"
    for value in facts.values():
        assert isinstance(value, str)


def test_the_adoption_did_not_make_any_state_protective():
    """The whole point: adoption may subtract, never add."""
    from src.live_order_state_model import (
        ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES,
    )
    from src.order_lifecycle_model import (
        LIFECYCLE_STATES_APPROVED_FOR_PROTECTION,
        PROVENANCE_SUFFICIENT_FOR_PROTECTION,
    )

    assert ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES == frozenset()
    assert LIFECYCLE_STATES_APPROVED_FOR_PROTECTION == frozenset()
    assert PROVENANCE_SUFFICIENT_FOR_PROTECTION == frozenset()


def test_a_vocabulary_disagreement_fails_closed():
    """If the class mapping reads a label as workable while the lifecycle
    model positively refuses it, the safe reading wins."""
    from src.live_order_state_model import (
        ProtectionEvidenceGap,
        ProtectiveCoverage,
        assess_protective_coverage,
    )

    # `pending_cancelled` is CANCELLATION_PENDING here and WORKING in the
    # lifecycle model; the existing pending-cancellation gate catches it first,
    # which is the correct precedence.
    assessment = assess_protective_coverage(
        state="pending_cancelled",
        side="sell",
        trigger="stop",
        stop_price=Decimal("100"),
        stop_submitted_quantity="1",
        stop_cumulative_quantity="0",
        verified_remaining_long_quantity="1",
    )
    assert assessment.coverage is ProtectiveCoverage.NOT_PROTECTED
    assert ProtectionEvidenceGap.CANCELLATION_PENDING in assessment.gaps


def test_a_hypothetically_approved_terminal_state_is_still_vetoed():
    """Even if someone approved a state the lifecycle model calls terminal,
    the independent second opinion refuses it."""
    from src.live_order_state_model import (
        ProtectionEvidenceGap,
        ProtectiveCoverage,
        assess_protective_coverage,
    )

    assessment = assess_protective_coverage(
        state="confirmed",
        side="sell",
        trigger="stop",
        stop_price=Decimal("100"),
        stop_submitted_quantity="1",
        stop_cumulative_quantity="0",
        verified_remaining_long_quantity="1",
        permitted_active_protection_states=frozenset({"confirmed"}),
    )
    # `confirmed` maps to WORKING, which is not vetoed, so a grounded approval
    # still works end to end. This is the property that keeps step 5 reachable.
    assert assessment.coverage is ProtectiveCoverage.VERIFIED_ACTIVE_PROTECTION
    assert ProtectionEvidenceGap.LIFECYCLE_MODEL_REFUSES_PROTECTION not in assessment.gaps
