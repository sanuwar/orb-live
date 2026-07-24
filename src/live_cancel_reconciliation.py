"""
Asynchronous cancellation reconciliation (PURE STATE MACHINE; OFFLINE).

The advertised cancel_equity_order response is a single boolean describing
whether the broker accepted a cancel REQUEST. Cancellation itself is
asynchronous, so that boolean can never settle the question this module
answers: what actually happened to the order?

The one rule everything else follows from:

    ACCEPTANCE IS NOT COMPLETION.

An accepted cancel request means a request is in flight. The final state is
established ONLY by a subsequent order-status read, and even then only when
the quantities reconcile. A fill that lands after the cancel request is a
RACE and is surfaced as one rather than absorbed into a tidy outcome.

This module performs NO broker, network, credential, token, or database
access. It submits nothing, cancels nothing, exits nothing, and retries
nothing. It is a classifier over evidence the caller already holds.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any

try:
    from .live_order_contract import (
        CancelAcceptance,
        QuantityDerivationError,
        derive_quantities,
    )
    from .live_order_state_model import OrderStateClass, classify_order_state
except ImportError:  # pragma: no cover - direct-module import fallback
    from live_order_contract import (
        CancelAcceptance,
        QuantityDerivationError,
        derive_quantities,
    )
    from live_order_state_model import OrderStateClass, classify_order_state


class CancelReconciliationState(str, Enum):
    """Every state the asynchronous cancellation contract can be in."""

    CANCEL_NOT_REQUESTED = "CANCEL_NOT_REQUESTED"
    CANCEL_REQUEST_ACCEPTED = "CANCEL_REQUEST_ACCEPTED"
    CANCELLATION_PENDING = "CANCELLATION_PENDING"
    CANCELLATION_VERIFIED = "CANCELLATION_VERIFIED"
    PARTIAL_FILL_WITH_REMAINDER_CANCELLED = (
        "PARTIAL_FILL_WITH_REMAINDER_CANCELLED"
    )
    FILL_RACED_CANCELLATION = "FILL_RACED_CANCELLATION"
    CANCELLATION_REJECTED = "CANCELLATION_REJECTED"
    INDETERMINATE = "INDETERMINATE"


# States that are simply not settled yet. The correct response is another
# order-status read, never an action.
UNRESOLVED_STATES: frozenset[CancelReconciliationState] = frozenset(
    {
        CancelReconciliationState.CANCEL_REQUEST_ACCEPTED,
        CancelReconciliationState.CANCELLATION_PENDING,
        CancelReconciliationState.INDETERMINATE,
    }
)

# States that must not be handled automatically.
OPERATOR_RESOLUTION_STATES: frozenset[CancelReconciliationState] = frozenset(
    {
        CancelReconciliationState.PARTIAL_FILL_WITH_REMAINDER_CANCELLED,
        CancelReconciliationState.FILL_RACED_CANCELLATION,
        CancelReconciliationState.CANCELLATION_REJECTED,
        CancelReconciliationState.INDETERMINATE,
    }
)


@dataclass(frozen=True)
class CancelReconciliationOutcome:
    """
    A pure classification. It authorizes nothing.

    ``reason`` is an authored, non-sensitive label. It never contains a
    quantity, an identifier, broker guide text, or a reject reason.
    """

    state: CancelReconciliationState
    reason: str

    @property
    def cancellation_verified(self) -> bool:
        """
        Whether cancellation is VERIFIED.

        True only for CANCELLATION_VERIFIED — a terminal cancelled state
        read back from the broker whose quantities reconcile. Never true
        merely because a cancel request was accepted.
        """
        return self.state is CancelReconciliationState.CANCELLATION_VERIFIED

    @property
    def authorizes_exit(self) -> bool:
        """
        ALWAYS FALSE, for every state, by design.

        No exit may be authorized from a cancellation classification. Exit
        authorization requires an approved bounded-risk invariant, an
        approved adapter contract, and explicit operator authorization —
        none of which exist. This property exists so the answer is
        explicit and testable rather than merely absent.
        """
        return False

    @property
    def is_unresolved(self) -> bool:
        return self.state in UNRESOLVED_STATES

    @property
    def requires_operator_resolution(self) -> bool:
        return self.state in OPERATOR_RESOLUTION_STATES


def _outcome(
    state: CancelReconciliationState, reason: str
) -> CancelReconciliationOutcome:
    return CancelReconciliationOutcome(state=state, reason=reason)


class _MalformedQuantityError(Exception):
    """Internal: an optional quantity was supplied but is unusable."""


def _optional_decimal(value: Any) -> Decimal | None:
    """
    Parse an optional non-negative quantity.

    ``None`` means "not supplied", which is a legitimate state. A supplied
    but unparseable value is NOT the same thing and must never collapse into
    it, so it raises instead.
    """
    if value is None:
        return None

    try:
        return derive_quantities(value, 0).submitted
    except QuantityDerivationError as error:
        raise _MalformedQuantityError from error


def reconcile_cancellation(
    *,
    cancel_requested: bool,
    acceptance: CancelAcceptance | None = None,
    observed_state: Any = None,
    observed_submitted_quantity: Any = None,
    observed_cumulative_quantity: Any = None,
    cumulative_quantity_at_request: Any = None,
    verified_position_quantity: Any = None,
) -> CancelReconciliationOutcome:
    """
    Classify the asynchronous cancellation contract from available evidence.

    ``observed_state`` and the two observed quantities come from a
    get_equity_orders read. When no read has happened yet they stay None and
    the outcome can only ever be unresolved.

    ``cumulative_quantity_at_request`` is the cumulative fill recorded at the
    moment the cancel was requested. When supplied, ANY increase beyond it is
    surfaced as FILL_RACED_CANCELLATION regardless of the reported state.

    ``verified_position_quantity`` is an independently verified position
    reading. PARTIAL_FILL_WITH_REMAINDER_CANCELLED is only confirmed when it
    is supplied AND agrees with the cumulative fill; without it the outcome
    is INDETERMINATE rather than assumed.
    """
    if not cancel_requested:
        if acceptance is not None:
            return _outcome(
                CancelReconciliationState.INDETERMINATE,
                "A cancel acceptance was supplied without a cancel request.",
            )
        return _outcome(
            CancelReconciliationState.CANCEL_NOT_REQUESTED,
            "No cancellation has been requested for this order.",
        )

    # ---- no order-status read yet ----
    if observed_state is None:
        if acceptance is CancelAcceptance.CANCEL_REQUEST_ACCEPTED_ASYNC:
            return _outcome(
                CancelReconciliationState.CANCEL_REQUEST_ACCEPTED,
                "The broker accepted the cancel request. Cancellation is "
                "asynchronous and remains unverified until an order-status "
                "read confirms a terminal state.",
            )

        if acceptance is CancelAcceptance.CANCEL_REQUEST_NOT_ACCEPTED:
            return _outcome(
                CancelReconciliationState.CANCELLATION_REJECTED,
                "The broker did not accept the cancel request. This is not "
                "a cancellation and must not be treated as one.",
            )

        return _outcome(
            CancelReconciliationState.INDETERMINATE,
            "A cancel was requested but no acceptance and no order-status "
            "observation are available.",
        )

    # ---- quantities from the order-status read ----
    try:
        observed = derive_quantities(
            observed_submitted_quantity, observed_cumulative_quantity
        )
    except QuantityDerivationError:
        return _outcome(
            CancelReconciliationState.INDETERMINATE,
            "The observed order quantities are missing, malformed, or "
            "internally inconsistent.",
        )

    try:
        baseline = _optional_decimal(cumulative_quantity_at_request)
    except _MalformedQuantityError:
        return _outcome(
            CancelReconciliationState.INDETERMINATE,
            "The cumulative fill recorded at cancel-request time is "
            "malformed.",
        )

    # ---- a fill after the request is always a race ----
    if baseline is not None and observed.cumulative > baseline:
        return _outcome(
            CancelReconciliationState.FILL_RACED_CANCELLATION,
            "The order filled further after the cancel request was made. "
            "This is a fill/cancel race and requires operator resolution.",
        )

    state_class = classify_order_state(observed_state)

    if state_class is OrderStateClass.CANCELLATION_PENDING:
        return _outcome(
            CancelReconciliationState.CANCELLATION_PENDING,
            "The broker reports the cancellation as still in flight. This "
            "is not a completed cancellation.",
        )

    if state_class is OrderStateClass.TERMINAL_FILLED:
        return _outcome(
            CancelReconciliationState.FILL_RACED_CANCELLATION,
            "The order reached a filled state despite the cancel request. "
            "This is a fill/cancel race and requires operator resolution.",
        )

    if state_class is OrderStateClass.TERMINAL_CANCELLED:
        if observed.cumulative > 0:
            return _outcome(
                CancelReconciliationState.FILL_RACED_CANCELLATION,
                "The order reports a cancelled state while also reporting "
                "filled shares. The fill must be resolved before the "
                "cancellation can be treated as clean.",
            )

        return _outcome(
            CancelReconciliationState.CANCELLATION_VERIFIED,
            "The broker reports a terminal cancelled state with no filled "
            "shares, and the quantities reconcile.",
        )

    if state_class is OrderStateClass.PARTIAL_FILL_WITH_REMAINDER_CANCELLED:
        if not (0 < observed.cumulative < observed.submitted):
            return _outcome(
                CancelReconciliationState.INDETERMINATE,
                "The partial-fill-with-remainder-cancelled state does not "
                "agree with the reported cumulative quantity.",
            )

        try:
            position = _optional_decimal(verified_position_quantity)
        except _MalformedQuantityError:
            return _outcome(
                CancelReconciliationState.INDETERMINATE,
                "The verified position quantity is malformed.",
            )

        if position is None:
            return _outcome(
                CancelReconciliationState.INDETERMINATE,
                "A partial fill with a cancelled remainder requires an "
                "independently verified position quantity, which was not "
                "supplied.",
            )

        if position != observed.cumulative:
            return _outcome(
                CancelReconciliationState.INDETERMINATE,
                "The verified position quantity disagrees with the "
                "cumulative filled quantity.",
            )

        return _outcome(
            CancelReconciliationState.PARTIAL_FILL_WITH_REMAINDER_CANCELLED,
            "Part of the order filled and the remainder was cancelled. The "
            "filled shares are a real position and require protection or "
            "operator resolution.",
        )

    if state_class is OrderStateClass.TERMINAL_REJECTED_OR_FAILED:
        return _outcome(
            CancelReconciliationState.INDETERMINATE,
            "The order reached a rejected, failed, or voided state, which "
            "is not a cancellation outcome.",
        )

    if state_class is OrderStateClass.ACTIVE_OR_WORKING:
        if acceptance is CancelAcceptance.CANCEL_REQUEST_ACCEPTED_ASYNC:
            return _outcome(
                CancelReconciliationState.CANCEL_REQUEST_ACCEPTED,
                "The cancel request was accepted but the order is still "
                "working. Cancellation remains unverified.",
            )

        if acceptance is CancelAcceptance.CANCEL_REQUEST_NOT_ACCEPTED:
            return _outcome(
                CancelReconciliationState.CANCELLATION_REJECTED,
                "The cancel request was not accepted and the order is still "
                "working.",
            )

        return _outcome(
            CancelReconciliationState.INDETERMINATE,
            "The order is still working and no cancel acceptance is "
            "available.",
        )

    return _outcome(
        CancelReconciliationState.INDETERMINATE,
        "The broker returned an order state that is not an advertised "
        "equity lifecycle state.",
    )
