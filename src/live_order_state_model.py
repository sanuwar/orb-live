"""
Conservative equity-order state classification (PURE; OFFLINE).

Two separate questions are answered here, and they are deliberately NOT the
same question:

  1. WHAT LIFECYCLE CLASS is a broker order state in?
     Grounded directly in the advertised output ``state`` description.

  2. IS A PROTECTIVE STOP VERIFIABLY PROTECTING A POSITION RIGHT NOW?
     NOT grounded by the schema, and therefore answered INDETERMINATE for
     every working state until a later adapter-initialization checkpoint
     grounds it.

Conflating the two is exactly the error this module exists to prevent. An
order being "open" says nothing about whether the venue is actually working
a stop that would fire. The schema advertises lifecycle names; it does not
advertise which of them guarantee an armed, resting protective order.

This module performs NO broker, network, credential, token, or database
access. Every function is pure. It authorizes nothing and submits nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any

try:
    from .live_broker_capabilities import APPROVED_PROTECTION_INVARIANT
    from .live_order_contract import (
        ADVERTISED_ORDER_OUTPUT_STATES,
        QuantityDerivationError,
        derive_quantities,
    )
    from .order_lifecycle_model import (
        LifecycleState,
        MappingProvenance,
        ProtectionVerdictReason,
        assess_protection,
        label_provenance,
        normalize_label,
    )
except ImportError:  # pragma: no cover - direct-module import fallback
    from live_broker_capabilities import APPROVED_PROTECTION_INVARIANT
    from live_order_contract import (
        ADVERTISED_ORDER_OUTPUT_STATES,
        QuantityDerivationError,
        derive_quantities,
    )
    from order_lifecycle_model import (
        LifecycleState,
        MappingProvenance,
        ProtectionVerdictReason,
        assess_protection,
        label_provenance,
        normalize_label,
    )


# --------------------------------------------------------------------------
# Lifecycle classification.
# --------------------------------------------------------------------------

class OrderStateClass(str, Enum):
    """Conservative lifecycle classes for an advertised equity-order state."""

    ACTIVE_OR_WORKING = "ACTIVE_OR_WORKING"
    CANCELLATION_PENDING = "CANCELLATION_PENDING"
    TERMINAL_CANCELLED = "TERMINAL_CANCELLED"
    TERMINAL_FILLED = "TERMINAL_FILLED"
    TERMINAL_REJECTED_OR_FAILED = "TERMINAL_REJECTED_OR_FAILED"
    PARTIAL_FILL_WITH_REMAINDER_CANCELLED = (
        "PARTIAL_FILL_WITH_REMAINDER_CANCELLED"
    )
    INDETERMINATE_OR_UNSUPPORTED = "INDETERMINATE_OR_UNSUPPORTED"


# Every mapping below is grounded in the advertised OUTPUT state list.
#
# `confirmed` is a special case: it appears in the get_equity_orders INPUT
# state FILTER but NOT in the output ``state`` description. It is mapped as
# working because the input filter advertises it as a live filterable state,
# and because the classification it receives grants nothing — every working
# state is INDETERMINATE for protection purposes. The asymmetry is recorded
# rather than resolved.
_STATE_CLASSES: dict[str, OrderStateClass] = {
    # Working.
    "new": OrderStateClass.ACTIVE_OR_WORKING,
    "queued": OrderStateClass.ACTIVE_OR_WORKING,
    "confirmed": OrderStateClass.ACTIVE_OR_WORKING,
    "unconfirmed": OrderStateClass.ACTIVE_OR_WORKING,
    "partially_filled": OrderStateClass.ACTIVE_OR_WORKING,
    # Short-sale locating is a pre-working state, not a protective one.
    "locating": OrderStateClass.ACTIVE_OR_WORKING,
    # Cancellation in flight.
    "pending_cancelled": OrderStateClass.CANCELLATION_PENDING,
    # Terminal.
    "cancelled": OrderStateClass.TERMINAL_CANCELLED,
    "filled": OrderStateClass.TERMINAL_FILLED,
    "rejected": OrderStateClass.TERMINAL_REJECTED_OR_FAILED,
    "failed": OrderStateClass.TERMINAL_REJECTED_OR_FAILED,
    "voided": OrderStateClass.TERMINAL_REJECTED_OR_FAILED,
    "locate_failed": OrderStateClass.TERMINAL_REJECTED_OR_FAILED,
    # Mixed terminal.
    "partially_filled_rest_cancelled": (
        OrderStateClass.PARTIAL_FILL_WITH_REMAINDER_CANCELLED
    ),
}

WORKING_ORDER_STATES: frozenset[str] = frozenset(
    state
    for state, classification in _STATE_CLASSES.items()
    if classification is OrderStateClass.ACTIVE_OR_WORKING
)

TERMINAL_ORDER_STATE_CLASSES: frozenset[OrderStateClass] = frozenset(
    {
        OrderStateClass.TERMINAL_CANCELLED,
        OrderStateClass.TERMINAL_FILLED,
        OrderStateClass.TERMINAL_REJECTED_OR_FAILED,
        OrderStateClass.PARTIAL_FILL_WITH_REMAINDER_CANCELLED,
    }
)


def normalize_state(value: Any) -> str:
    """
    Normalize a broker state for lookup.

    Deliberately conservative: it strips and lowercases and does NOTHING
    else. In particular it does not alias unadvertised spellings such as the
    single-l "canceled" into an advertised terminal state. An unadvertised
    spelling is an unknown state, and unknown states fail closed.
    """
    if value is None:
        return ""

    if isinstance(value, bool) or not isinstance(value, str):
        return ""

    return value.strip().lower()


def classify_order_state(state: Any) -> OrderStateClass:
    """
    Classify a broker order state, failing closed on anything unrecognized.

    Any state outside the advertised output set — including a misspelling, a
    future broker addition, a blank, or a non-string — is
    INDETERMINATE_OR_UNSUPPORTED. It is never optimistically bucketed.
    """
    return _STATE_CLASSES.get(
        normalize_state(state), OrderStateClass.INDETERMINATE_OR_UNSUPPORTED
    )


def is_advertised_state(state: Any) -> bool:
    """Whether the state appears in the advertised OUTPUT state description."""
    return normalize_state(state) in ADVERTISED_ORDER_OUTPUT_STATES


# --------------------------------------------------------------------------
# Protective-coverage assessment.
# --------------------------------------------------------------------------

class ProtectiveCoverage(str, Enum):
    """
    Whether a protective stop is verifiably covering a position.

    INDETERMINATE is not a soft NOT_PROTECTED. It means the available
    evidence cannot decide, which for a protective stop must be handled as
    seriously as NOT_PROTECTED.
    """

    VERIFIED_ACTIVE_PROTECTION = "VERIFIED_ACTIVE_PROTECTION"
    NOT_PROTECTED = "NOT_PROTECTED"
    INDETERMINATE = "INDETERMINATE"


# DELIBERATELY EMPTY.
#
# The official schema advertises lifecycle NAMES. It does not establish
# which working state means "this stop is armed and resting at the venue and
# would fire". A stop sitting in `new`, `queued`, or `unconfirmed` may not
# be working yet. Because nothing grounds that mapping, no working state may
# yield VERIFIED_ACTIVE_PROTECTION, and every one of them assesses as
# INDETERMINATE.
#
# Populating this set requires a later, separately reviewed, NON-ORDER
# adapter-initialization checkpoint. It must not be filled in from the
# schema, from inference, or from a single observation.
ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES: frozenset[str] = frozenset()


# --------------------------------------------------------------------------
# Independent second opinion from the canonical lifecycle model.
# --------------------------------------------------------------------------
#
# `src/order_lifecycle_model.py` answers *what happened to an order* from a
# separate vocabulary, with its own per-mapping provenance. It is adopted here
# in the narrowest way that is still load-bearing: it may **veto** a protection
# claim, and it may never grant one.
#
# The asymmetry is the point. A model that could grant would need evidence
# nothing here has, and `PROVENANCE_SUFFICIENT_FOR_PROTECTION` in that module
# is empty precisely because no provenance level qualifies. A model that can
# only subtract adds a real barrier without inventing authority: if the two
# vocabularies disagree — this module classifying a label as working while the
# lifecycle model reads it as canceled, replaced, or unrecognized — the
# disagreement itself is disqualifying, and the safe reading wins.
#
# It also deliberately does NOT veto `WORKING`. `WORKING` is the state a future
# grounded approval would legitimately name, and vetoing it here would make
# step 5 of the Milestone 9 definition of done unreachable — populating
# ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES would no longer be able to enable
# anything. Adoption must not quietly foreclose the milestone it serves.


@dataclass(frozen=True)
class LifecycleProjection:
    """What the canonical lifecycle model says about one broker label.

    Reporting only. `is_protective` and `permits_automated_action` are fixed
    `False` and are asserted rather than computed hopefully: nothing in this
    projection may become an authority to act.
    """

    lifecycle_state: LifecycleState
    provenance: MappingProvenance
    cannot_protect: bool
    is_protective: bool = False
    permits_automated_action: bool = False

    def __post_init__(self) -> None:
        if self.is_protective or self.permits_automated_action:
            raise ValueError(
                "a lifecycle projection may never assert protection or action"
            )

    def sanitized_facts(self) -> dict[str, str]:
        """Fixed keys and values. No identifier, symbol, quantity, or price."""
        return {
            "lifecycle_state": self.lifecycle_state.value,
            "lifecycle_provenance": self.provenance.value,
            "lifecycle_cannot_protect": "true" if self.cannot_protect else "false",
            "permits_automated_action": "false",
        }


#: The lifecycle verdicts that constitute a positive refusal — the model knows
#: this order is not protecting anything. `LIFECYCLE_STATE_CANNOT_ESTABLISH_
#: PROTECTION`, which is where `WORKING` lands, is deliberately absent: it
#: means "cannot tell from lifecycle alone", not "definitely not".
LIFECYCLE_REFUSAL_REASONS: frozenset[ProtectionVerdictReason] = frozenset({
    ProtectionVerdictReason.ORDER_IS_TERMINAL,
    ProtectionVerdictReason.ORDER_WAS_REPLACED,
    ProtectionVerdictReason.STATE_IS_UNKNOWN,
})


def lifecycle_projection(state: Any) -> LifecycleProjection:
    """Project a broker label onto the canonical lifecycle model.

    An unrecognized label yields `UNKNOWN` with `ABSENT` provenance and is
    refused — never a guessed canonical state.
    """
    lifecycle_state = normalize_label(state)
    verdict = assess_protection(lifecycle_state)
    # Structural, not conventional: if the lifecycle model ever returned a
    # protective verdict, this module must not silently pass it through.
    if verdict.is_protective:  # pragma: no cover - unreachable by construction
        raise ValueError("the lifecycle model may not grant protection here")
    return LifecycleProjection(
        lifecycle_state=lifecycle_state,
        provenance=label_provenance(state),
        cannot_protect=verdict.reason in LIFECYCLE_REFUSAL_REASONS,
    )


class ProtectionEvidenceGap(str, Enum):
    """Why coverage could not be verified. Reason codes only, no values."""

    NOT_A_SELL_ORDER = "NOT_A_SELL_ORDER"
    NO_STOP_TRIGGER = "NO_STOP_TRIGGER"
    NO_STOP_PRICE = "NO_STOP_PRICE"
    QUANTITY_DISAGREEMENT = "QUANTITY_DISAGREEMENT"
    NO_POSITION_TO_PROTECT = "NO_POSITION_TO_PROTECT"
    MALFORMED_QUANTITY = "MALFORMED_QUANTITY"
    CANCELLATION_PENDING = "CANCELLATION_PENDING"
    TERMINAL_STATE = "TERMINAL_STATE"
    UNRECOGNIZED_STATE = "UNRECOGNIZED_STATE"
    STATE_NOT_GROUNDED_AS_ACTIVE_PROTECTION = (
        "STATE_NOT_GROUNDED_AS_ACTIVE_PROTECTION"
    )
    #: The canonical lifecycle model positively refuses this state — terminal,
    #: replaced, or unrecognized. Raised independently of the class mapping
    #: above, so a disagreement between the two vocabularies fails closed.
    LIFECYCLE_MODEL_REFUSES_PROTECTION = "LIFECYCLE_MODEL_REFUSES_PROTECTION"


@dataclass(frozen=True)
class ProtectiveCoverageAssessment:
    """
    A pure report. It authorizes nothing and triggers nothing.

    ``invariant`` is the APPROVED invariant, quoted verbatim. This module
    reports against it; it does not weaken, reinterpret, or amend it.
    """

    coverage: ProtectiveCoverage
    state_class: OrderStateClass
    gaps: tuple[ProtectionEvidenceGap, ...]
    invariant: str = APPROVED_PROTECTION_INVARIANT

    @property
    def gap_codes(self) -> tuple[str, ...]:
        return tuple(gap.value for gap in self.gaps)

    @property
    def is_verified(self) -> bool:
        return self.coverage is ProtectiveCoverage.VERIFIED_ACTIVE_PROTECTION


def _assessment(
    coverage: ProtectiveCoverage,
    state_class: OrderStateClass,
    gaps: tuple[ProtectionEvidenceGap, ...],
) -> ProtectiveCoverageAssessment:
    return ProtectiveCoverageAssessment(
        coverage=coverage, state_class=state_class, gaps=gaps
    )


def assess_protective_coverage(
    *,
    state: Any,
    side: Any,
    trigger: Any,
    stop_price: Decimal | None,
    stop_submitted_quantity: Any,
    stop_cumulative_quantity: Any,
    verified_remaining_long_quantity: Any,
    permitted_active_protection_states: frozenset[str] = (
        ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES
    ),
) -> ProtectiveCoverageAssessment:
    """
    Assess whether a stop order verifiably protects a long position.

    VERIFIED_ACTIVE_PROTECTION requires ALL of:

      * a known protective SELL order;
      * an established stop trigger (trigger 'stop' AND a positive stop
        price);
      * EXACT coverage of the verified remaining long position — the stop's
        own remaining quantity must equal it, never more and never less;
      * a broker state explicitly permitted by an approved adapter contract;
      * no pending cancellation;
      * no terminal state;
      * no quantity disagreement.

    Because ``permitted_active_protection_states`` defaults to the empty
    grounded set, every working state assesses as INDETERMINATE today. That
    is the intended behaviour, not a gap to route around.
    """
    state_class = classify_order_state(state)
    gaps: list[ProtectionEvidenceGap] = []

    # ---- shape of the order itself ----
    normalized_side = normalize_state(side)
    if normalized_side != "sell":
        gaps.append(ProtectionEvidenceGap.NOT_A_SELL_ORDER)

    if normalize_state(trigger) != "stop":
        gaps.append(ProtectionEvidenceGap.NO_STOP_TRIGGER)

    if stop_price is None or stop_price <= 0:
        gaps.append(ProtectionEvidenceGap.NO_STOP_PRICE)

    # ---- exact quantity coverage ----
    try:
        stop_quantities = derive_quantities(
            stop_submitted_quantity, stop_cumulative_quantity
        )
        position = derive_quantities(
            verified_remaining_long_quantity, 0
        ).submitted
    except QuantityDerivationError:
        gaps.append(ProtectionEvidenceGap.MALFORMED_QUANTITY)
        # Without trustworthy quantities nothing can be verified, and the
        # order shape alone must not decide.
        return _assessment(
            ProtectiveCoverage.INDETERMINATE, state_class, tuple(gaps)
        )

    if position <= 0:
        gaps.append(ProtectionEvidenceGap.NO_POSITION_TO_PROTECT)
    elif stop_quantities.remaining != position:
        gaps.append(ProtectionEvidenceGap.QUANTITY_DISAGREEMENT)

    # ---- lifecycle ----
    if state_class is OrderStateClass.CANCELLATION_PENDING:
        gaps.append(ProtectionEvidenceGap.CANCELLATION_PENDING)
        return _assessment(
            ProtectiveCoverage.NOT_PROTECTED, state_class, tuple(gaps)
        )

    if state_class in TERMINAL_ORDER_STATE_CLASSES:
        gaps.append(ProtectionEvidenceGap.TERMINAL_STATE)
        return _assessment(
            ProtectiveCoverage.NOT_PROTECTED, state_class, tuple(gaps)
        )

    if state_class is OrderStateClass.INDETERMINATE_OR_UNSUPPORTED:
        gaps.append(ProtectionEvidenceGap.UNRECOGNIZED_STATE)
        return _assessment(
            ProtectiveCoverage.INDETERMINATE, state_class, tuple(gaps)
        )

    # An independent second opinion, from a separate vocabulary. Reaching here
    # means the class mapping above reads this state as live and workable; if
    # the canonical lifecycle model positively refuses it — terminal, replaced,
    # or unrecognized — the two disagree about what the label means, and a
    # disagreement about protection is settled by the safe reading.
    if lifecycle_projection(state).cannot_protect:
        gaps.append(ProtectionEvidenceGap.LIFECYCLE_MODEL_REFUSES_PROTECTION)
        return _assessment(
            ProtectiveCoverage.NOT_PROTECTED, state_class, tuple(gaps)
        )

    # ---- definite failures of the order's own shape ----
    # A non-sell order, a missing stop trigger, or a quantity that does not
    # match the position are positive DISPROOFS of coverage. They are not
    # ambiguity.
    disproofs = {
        ProtectionEvidenceGap.NOT_A_SELL_ORDER,
        ProtectionEvidenceGap.NO_STOP_TRIGGER,
        ProtectionEvidenceGap.NO_STOP_PRICE,
        ProtectionEvidenceGap.QUANTITY_DISAGREEMENT,
        ProtectionEvidenceGap.NO_POSITION_TO_PROTECT,
    }
    if disproofs & set(gaps):
        return _assessment(
            ProtectiveCoverage.NOT_PROTECTED, state_class, tuple(gaps)
        )

    # ---- the ungrounded step ----
    if normalize_state(state) not in permitted_active_protection_states:
        gaps.append(
            ProtectionEvidenceGap.STATE_NOT_GROUNDED_AS_ACTIVE_PROTECTION
        )
        return _assessment(
            ProtectiveCoverage.INDETERMINATE, state_class, tuple(gaps)
        )

    return _assessment(
        ProtectiveCoverage.VERIFIED_ACTIVE_PROTECTION, state_class, ()
    )
