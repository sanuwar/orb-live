"""Offline order lifecycle model — canonical states, transitions, reconciliation.

This module answers *what happened to an order*, and refuses to answer *is that
order protecting a position*. The second question is not harder; it is a
different question, and this model does not have the evidence to answer it.

## The vocabulary problem this exists to make visible

The broker advertises 13 output labels. Five of the ten canonical lifecycle
states are **not among them**:

* `FILLED` and `CANCELED` correspond to labels seen in completed sanitized runs.
* `PARTIALLY_FILLED` and the rejection family appear in the advertised schema
  but have never been observed here.
* `SUBMITTED`, `ACCEPTED`, and `WORKING` are canonical stages the broker's
  vocabulary does not name. Mapping `unconfirmed` or `confirmed` onto them is
  an **inference**, not a reading.
* `EXPIRED` and `REPLACED` have **no broker label at all**.

So provenance is carried **per mapping**, not per state. Asserting
`confirmed → WORKING` is the same class of unproven claim that keeps
`ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES` empty, and this module must not
launder it into something that looks established.

## What this module will not do

`WORKING` sounds like "armed at the venue". It is not, and
`assess_protection` returns a hard `False` for it — not an exception, not a
refusal, just `False` with a fixed reason saying that lifecycle state alone
cannot establish protection and that the read-only grounding checkpoint is
where that answer comes from.

`permits_automated_action` is `False` on every result this module can produce,
because it derives from an approved set that is empty rather than from whether
a transition was legal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping


class OrderLifecycleError(RuntimeError):
    """A malformed model input. Never carries broker text."""


# --------------------------------------------------------------------------
# States.
# --------------------------------------------------------------------------


class LifecycleState(str, Enum):
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    WORKING = "WORKING"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    REPLACED = "REPLACED"
    UNKNOWN = "UNKNOWN"


#: A state no further transition may leave. `REPLACED` is terminal *for this
#: order identifier* — the chain continues under a different one.
TERMINAL_STATES: frozenset[LifecycleState] = frozenset({
    LifecycleState.FILLED,
    LifecycleState.CANCELED,
    LifecycleState.REJECTED,
    LifecycleState.EXPIRED,
    LifecycleState.REPLACED,
})


class MappingProvenance(str, Enum):
    """How much is actually known about a broker-label → canonical mapping."""

    #: The broker label was seen in completed sanitized run evidence.
    OBSERVED_LIVE = "OBSERVED_LIVE"
    #: The label appears in the advertised output schema, never seen here.
    SCHEMA_ONLY = "SCHEMA_ONLY"
    #: A canonical stage the broker's vocabulary does not name. The mapping is
    #: our interpretation, and interpretation is not evidence.
    INFERRED_UNPROVEN = "INFERRED_UNPROVEN"
    #: No broker label produces this state at all.
    ABSENT = "ABSENT"


#: Provenance strong enough to support a protection claim. Deliberately empty:
#: no provenance level defined here qualifies, and adding one would require
#: evidence this repository does not have.
PROVENANCE_SUFFICIENT_FOR_PROTECTION: frozenset[MappingProvenance] = frozenset()


# --------------------------------------------------------------------------
# Normalization: broker label -> canonical state, with provenance.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LabelMapping:
    state: LifecycleState
    provenance: MappingProvenance


def _m(state: LifecycleState, provenance: MappingProvenance) -> LabelMapping:
    return LabelMapping(state, provenance)


#: Every advertised broker label, plus `confirmed`, which is not advertised but
#: was observed in completed runs. A label absent from this table normalizes to
#: `UNKNOWN` — never to a guessed canonical state.
LABEL_MAPPINGS: Mapping[str, LabelMapping] = MappingProxyType({
    # Observed in completed sanitized runs.
    "filled": _m(LifecycleState.FILLED, MappingProvenance.OBSERVED_LIVE),
    "cancelled": _m(LifecycleState.CANCELED, MappingProvenance.OBSERVED_LIVE),
    # Observed as labels, but the lifecycle stage they imply is our reading,
    # not the broker's statement. Provenance records the weaker of the two.
    "unconfirmed": _m(LifecycleState.SUBMITTED, MappingProvenance.INFERRED_UNPROVEN),
    "confirmed": _m(LifecycleState.WORKING, MappingProvenance.INFERRED_UNPROVEN),
    # Advertised but never seen, and the stage is likewise inferred.
    "new": _m(LifecycleState.SUBMITTED, MappingProvenance.INFERRED_UNPROVEN),
    "queued": _m(LifecycleState.ACCEPTED, MappingProvenance.INFERRED_UNPROVEN),
    "locating": _m(LifecycleState.ACCEPTED, MappingProvenance.INFERRED_UNPROVEN),
    "pending_cancelled": _m(LifecycleState.WORKING, MappingProvenance.INFERRED_UNPROVEN),
    # Advertised, never seen, but the stage is unambiguous from the label.
    "partially_filled": _m(
        LifecycleState.PARTIALLY_FILLED, MappingProvenance.SCHEMA_ONLY
    ),
    "partially_filled_rest_cancelled": _m(
        LifecycleState.CANCELED, MappingProvenance.SCHEMA_ONLY
    ),
    "rejected": _m(LifecycleState.REJECTED, MappingProvenance.SCHEMA_ONLY),
    "failed": _m(LifecycleState.REJECTED, MappingProvenance.SCHEMA_ONLY),
    "locate_failed": _m(LifecycleState.REJECTED, MappingProvenance.SCHEMA_ONLY),
    "voided": _m(LifecycleState.REJECTED, MappingProvenance.SCHEMA_ONLY),
})


#: Canonical states no broker label produces. `EXPIRED` and `REPLACED` are
#: modelled because a lifecycle must be able to represent them, not because
#: anything has ever emitted them.
ABSENT_STATES: frozenset[LifecycleState] = frozenset({
    LifecycleState.EXPIRED,
    LifecycleState.REPLACED,
})

_PROVENANCE_STRENGTH = {
    MappingProvenance.OBSERVED_LIVE: 3,
    MappingProvenance.SCHEMA_ONLY: 2,
    MappingProvenance.INFERRED_UNPROVEN: 1,
    MappingProvenance.ABSENT: 0,
}


def state_provenance(state: LifecycleState) -> MappingProvenance:
    """The strongest provenance any label offers for this state.

    Derived rather than restated, so a mapping change cannot leave a stale
    provenance claim behind.
    """
    if state is LifecycleState.UNKNOWN:
        return MappingProvenance.ABSENT
    candidates = [
        mapping.provenance
        for mapping in LABEL_MAPPINGS.values()
        if mapping.state is state
    ]
    if not candidates:
        return MappingProvenance.ABSENT
    return max(candidates, key=lambda item: _PROVENANCE_STRENGTH[item])


def normalize_label(label: Any) -> LifecycleState:
    """Broker label to canonical state. Unrecognized input becomes UNKNOWN."""
    if not isinstance(label, str):
        return LifecycleState.UNKNOWN
    mapping = LABEL_MAPPINGS.get(label.strip().lower())
    return mapping.state if mapping else LifecycleState.UNKNOWN


def label_provenance(label: Any) -> MappingProvenance:
    if not isinstance(label, str):
        return MappingProvenance.ABSENT
    mapping = LABEL_MAPPINGS.get(label.strip().lower())
    return mapping.provenance if mapping else MappingProvenance.ABSENT


# --------------------------------------------------------------------------
# Protection: the question this module answers with a flat no.
# --------------------------------------------------------------------------


#: Derived from provenance, and empty because no provenance level qualifies.
#: This set exists so the emptiness is *computed* rather than asserted, and so
#: a test can prove it cannot silently gain a member.
LIFECYCLE_STATES_APPROVED_FOR_PROTECTION: frozenset[LifecycleState] = frozenset(
    state
    for state in LifecycleState
    if state_provenance(state) in PROVENANCE_SUFFICIENT_FOR_PROTECTION
)


class ProtectionVerdictReason(str, Enum):
    """Why a protection assessment came back false. Always false, for now."""

    #: The load-bearing one. `WORKING` lands here.
    LIFECYCLE_STATE_CANNOT_ESTABLISH_PROTECTION = (
        "LIFECYCLE_STATE_CANNOT_ESTABLISH_PROTECTION"
    )
    #: A terminal order is not protecting anything.
    ORDER_IS_TERMINAL = "ORDER_IS_TERMINAL"
    #: Replacement ends this identifier's life; the successor is a new order.
    ORDER_WAS_REPLACED = "ORDER_WAS_REPLACED"
    #: Nothing is known about the order's state.
    STATE_IS_UNKNOWN = "STATE_IS_UNKNOWN"


PROTECTION_EXPLANATION = (
    "Lifecycle state alone cannot establish that an order is armed, active, "
    "venue-confirmed, or protective. Protection requires the separate "
    "read-only grounding checkpoint; until it has run and a human has "
    "approved specific states, no lifecycle state implies protection."
)


@dataclass(frozen=True)
class ProtectionVerdict:
    is_protective: bool
    reason: ProtectionVerdictReason
    explanation: str = PROTECTION_EXPLANATION

    def __post_init__(self) -> None:
        # Structural, not conventional: nothing in this module may construct a
        # protective verdict while the approved set is empty.
        if self.is_protective and not LIFECYCLE_STATES_APPROVED_FOR_PROTECTION:
            raise OrderLifecycleError(
                "no lifecycle state is approved for protection"
            )


def assess_protection(state: LifecycleState) -> ProtectionVerdict:
    """Always false. Never raises merely because the state is WORKING.

    `WORKING` is the case this function exists for. The name invites the
    assumption that the order is resting at the venue and would fire; that
    assumption is exactly what has never been established here. So the answer
    is a plain `False` with a reason, not an exception — a caller asking the
    question deserves an answer it can log and move on from.
    """
    if not isinstance(state, LifecycleState):
        raise OrderLifecycleError("protection assessment needs a LifecycleState")
    if state is LifecycleState.REPLACED:
        return ProtectionVerdict(False, ProtectionVerdictReason.ORDER_WAS_REPLACED)
    if state in TERMINAL_STATES:
        return ProtectionVerdict(False, ProtectionVerdictReason.ORDER_IS_TERMINAL)
    if state is LifecycleState.UNKNOWN:
        return ProtectionVerdict(False, ProtectionVerdictReason.STATE_IS_UNKNOWN)
    return ProtectionVerdict(
        False, ProtectionVerdictReason.LIFECYCLE_STATE_CANNOT_ESTABLISH_PROTECTION
    )


# --------------------------------------------------------------------------
# Transitions.
# --------------------------------------------------------------------------

_S = LifecycleState

#: Allowed forward transitions. Terminal states map to an empty set, which is
#: what makes "no exit from terminal" a property of the data rather than a
#: special case in the checker.
ALLOWED_TRANSITIONS: Mapping[LifecycleState, frozenset[LifecycleState]] = (
    MappingProxyType({
        _S.SUBMITTED: frozenset({
            _S.ACCEPTED, _S.REJECTED, _S.CANCELED, _S.EXPIRED, _S.UNKNOWN,
        }),
        _S.ACCEPTED: frozenset({
            _S.WORKING, _S.PARTIALLY_FILLED, _S.FILLED, _S.CANCELED,
            _S.REJECTED, _S.EXPIRED, _S.REPLACED, _S.UNKNOWN,
        }),
        _S.WORKING: frozenset({
            _S.PARTIALLY_FILLED, _S.FILLED, _S.CANCELED, _S.EXPIRED,
            _S.REPLACED, _S.UNKNOWN,
        }),
        _S.PARTIALLY_FILLED: frozenset({
            _S.PARTIALLY_FILLED, _S.FILLED, _S.CANCELED, _S.EXPIRED,
            _S.REPLACED, _S.UNKNOWN,
        }),
        _S.FILLED: frozenset(),
        _S.CANCELED: frozenset(),
        _S.REJECTED: frozenset(),
        _S.EXPIRED: frozenset(),
        _S.REPLACED: frozenset(),
        # A later authoritative read may resolve an unknown to anything. The
        # result is flagged so a caller knows the history has a hole in it.
        _S.UNKNOWN: frozenset(LifecycleState),
    })
)


#: Transitions that run the lifecycle backwards. Listed explicitly rather than
#: derived from an ordering, because the lifecycle is not a total order and a
#: derived rule would be wrong in both directions at the branches.
BACKWARD_TRANSITIONS: frozenset[tuple[LifecycleState, LifecycleState]] = frozenset({
    (_S.ACCEPTED, _S.SUBMITTED),
    (_S.WORKING, _S.SUBMITTED),
    (_S.WORKING, _S.ACCEPTED),
    (_S.PARTIALLY_FILLED, _S.SUBMITTED),
    (_S.PARTIALLY_FILLED, _S.ACCEPTED),
    (_S.PARTIALLY_FILLED, _S.WORKING),
})


def transition_is_allowed(previous: LifecycleState, current: LifecycleState) -> bool:
    if not isinstance(previous, LifecycleState) or not isinstance(current, LifecycleState):
        raise OrderLifecycleError("transition needs two LifecycleState values")
    return current in ALLOWED_TRANSITIONS[previous]


def transition_is_backward(
    previous: LifecycleState, current: LifecycleState
) -> bool:
    return (previous, current) in BACKWARD_TRANSITIONS


# --------------------------------------------------------------------------
# Observations.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class OrderObservation:
    """One sighting of one order.

    `identifier` is accepted for correlation and is **never** persisted. The
    sanitized projection reports only that it was present and consistent.
    """

    identifier: str
    state: LifecycleState
    cumulative_filled: int = 0
    observed_monotonic: float = 0.0
    replaces_identifier: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.identifier, str) or not self.identifier.strip():
            raise OrderLifecycleError("an order identifier is required")
        if not isinstance(self.state, LifecycleState):
            raise OrderLifecycleError("state must be a LifecycleState")
        if not isinstance(self.cumulative_filled, int) or isinstance(
            self.cumulative_filled, bool
        ):
            raise OrderLifecycleError("cumulative fill must be an int")
        if self.cumulative_filled < 0:
            raise OrderLifecycleError("cumulative fill must not be negative")
        if self.replaces_identifier is not None and (
            not isinstance(self.replaces_identifier, str)
            or not self.replaces_identifier.strip()
        ):
            raise OrderLifecycleError("replacement parent identifier is invalid")
        if self.replaces_identifier == self.identifier:
            raise OrderLifecycleError("an order cannot replace itself")


# --------------------------------------------------------------------------
# Reconciliation.
# --------------------------------------------------------------------------


class ReconciliationOutcome(str, Enum):
    CONSISTENT_PROGRESS = "CONSISTENT_PROGRESS"
    NO_CHANGE = "NO_CHANGE"
    IMPOSSIBLE_TRANSITION = "IMPOSSIBLE_TRANSITION"
    BACKWARD_TRANSITION = "BACKWARD_TRANSITION"
    CONFLICTING_TERMINAL_STATES = "CONFLICTING_TERMINAL_STATES"
    MISSING_IDENTIFIER = "MISSING_IDENTIFIER"
    STALE_OBSERVATION = "STALE_OBSERVATION"
    AMBIGUOUS_REPLACEMENT_CHAIN = "AMBIGUOUS_REPLACEMENT_CHAIN"
    UNKNOWN_BROKER_STATE = "UNKNOWN_BROKER_STATE"
    QUANTITY_REGRESSION = "QUANTITY_REGRESSION"


#: Outcomes that are not themselves alarming. Neither permits action.
BENIGN_OUTCOMES: frozenset[ReconciliationOutcome] = frozenset({
    ReconciliationOutcome.CONSISTENT_PROGRESS,
    ReconciliationOutcome.NO_CHANGE,
})


@dataclass(frozen=True)
class ReconciliationResult:
    outcome: ReconciliationOutcome
    state: LifecycleState
    resolved_from_unknown: bool = False
    fill_occurred: bool = False
    permits_automated_action: bool = False

    def __post_init__(self) -> None:
        if self.permits_automated_action:
            raise OrderLifecycleError(
                "no reconciliation result may permit automated action"
            )

    def sanitized_facts(self) -> dict[str, str]:
        """Fixed keys, fixed values. No identifier, quantity, symbol, or price.

        Quantities are reported as *whether* a fill occurred, never how much:
        the exact number is not needed to decide anything here and is one more
        value that would have to be protected.
        """
        return {
            "reconciliation_outcome": self.outcome.value,
            "lifecycle_state": self.state.value,
            "state_provenance": state_provenance(self.state).value,
            "is_terminal": "true" if self.state in TERMINAL_STATES else "false",
            "resolved_from_unknown": "true" if self.resolved_from_unknown else "false",
            "fill_occurred": "true" if self.fill_occurred else "false",
            "permits_automated_action": "false",
        }


def reconcile(
    previous: OrderObservation,
    current: OrderObservation,
    *,
    maximum_age_seconds: float = 5.0,
) -> ReconciliationResult:
    """Compare two sightings of the same order and fail closed on anything odd.

    Evaluation order matters: identity, then freshness, then quantity, then
    the transition itself. A transition verdict drawn from mismatched or stale
    observations would look like a finding while describing nothing.
    """
    if not isinstance(previous, OrderObservation) or not isinstance(
        current, OrderObservation
    ):
        raise OrderLifecycleError("reconciliation needs two observations")

    if previous.identifier != current.identifier:
        return ReconciliationResult(
            ReconciliationOutcome.MISSING_IDENTIFIER, LifecycleState.UNKNOWN
        )

    if current.observed_monotonic < previous.observed_monotonic:
        return ReconciliationResult(
            ReconciliationOutcome.STALE_OBSERVATION, LifecycleState.UNKNOWN
        )
    if current.observed_monotonic - previous.observed_monotonic > maximum_age_seconds:
        return ReconciliationResult(
            ReconciliationOutcome.STALE_OBSERVATION, LifecycleState.UNKNOWN
        )

    if current.cumulative_filled < previous.cumulative_filled:
        # Quantity does not un-fill. Something is wrong with one of the reads.
        return ReconciliationResult(
            ReconciliationOutcome.QUANTITY_REGRESSION, LifecycleState.UNKNOWN
        )

    filled_more = current.cumulative_filled > previous.cumulative_filled
    resolved = previous.state is LifecycleState.UNKNOWN

    if (
        previous.state in TERMINAL_STATES
        and current.state in TERMINAL_STATES
        and previous.state is not current.state
    ):
        return ReconciliationResult(
            ReconciliationOutcome.CONFLICTING_TERMINAL_STATES, LifecycleState.UNKNOWN
        )

    if previous.state is current.state:
        if current.state is LifecycleState.UNKNOWN:
            return ReconciliationResult(
                ReconciliationOutcome.UNKNOWN_BROKER_STATE, LifecycleState.UNKNOWN
            )
        outcome = (
            ReconciliationOutcome.CONSISTENT_PROGRESS
            if filled_more
            else ReconciliationOutcome.NO_CHANGE
        )
        return ReconciliationResult(
            outcome, current.state, resolved_from_unknown=False, fill_occurred=filled_more
        )

    if transition_is_backward(previous.state, current.state):
        return ReconciliationResult(
            ReconciliationOutcome.BACKWARD_TRANSITION, LifecycleState.UNKNOWN
        )
    if not transition_is_allowed(previous.state, current.state):
        return ReconciliationResult(
            ReconciliationOutcome.IMPOSSIBLE_TRANSITION, LifecycleState.UNKNOWN
        )
    if current.state is LifecycleState.UNKNOWN:
        return ReconciliationResult(
            ReconciliationOutcome.UNKNOWN_BROKER_STATE, LifecycleState.UNKNOWN
        )

    return ReconciliationResult(
        ReconciliationOutcome.CONSISTENT_PROGRESS,
        current.state,
        resolved_from_unknown=resolved,
        fill_occurred=filled_more,
    )


# --------------------------------------------------------------------------
# Replacement chains.
# --------------------------------------------------------------------------

#: A chain longer than this is treated as ambiguous rather than walked. Real
#: replacement chains are short; a long one usually means a loop or a
#: correlation mistake, and following it would turn a data problem into a
#: traversal problem.
MAXIMUM_REPLACEMENT_DEPTH = 4

#: Only these states may be superseded. A terminal order cannot be replaced —
#: it has already ended — and an unknown one cannot be reasoned about.
REPLACEABLE_STATES: frozenset[LifecycleState] = frozenset({
    LifecycleState.ACCEPTED,
    LifecycleState.WORKING,
    LifecycleState.PARTIALLY_FILLED,
})


class ChainOutcome(str, Enum):
    RESOLVED = "RESOLVED"
    NO_REPLACEMENT = "NO_REPLACEMENT"
    MISSING_PARENT = "MISSING_PARENT"
    DUPLICATE_CHILD = "DUPLICATE_CHILD"
    CYCLE_DETECTED = "CYCLE_DETECTED"
    EXCESSIVE_DEPTH = "EXCESSIVE_DEPTH"
    INVALID_PARENT_STATE = "INVALID_PARENT_STATE"


@dataclass(frozen=True)
class ChainResult:
    outcome: ChainOutcome
    depth: int = 0
    terminal_state: LifecycleState = LifecycleState.UNKNOWN
    permits_automated_action: bool = False

    def __post_init__(self) -> None:
        if self.permits_automated_action:
            raise OrderLifecycleError("no chain result may permit automated action")

    def sanitized_facts(self) -> dict[str, str]:
        return {
            "chain_outcome": self.outcome.value,
            "chain_depth_bucket": (
                "none" if self.depth == 0 else "one" if self.depth == 1 else "many"
            ),
            "lifecycle_state": self.terminal_state.value,
            "permits_automated_action": "false",
        }


def resolve_replacement_chain(
    observations: Mapping[str, OrderObservation],
    root_identifier: str,
    *,
    maximum_depth: int = MAXIMUM_REPLACEMENT_DEPTH,
) -> ChainResult:
    """Walk a replacement chain from its root, refusing anything ambiguous.

    A replacement is not a state change — it ends one order identifier and
    begins another. Treating the pair as one order is how a cancel gets
    applied to something that no longer exists.
    """
    if not isinstance(observations, Mapping):
        raise OrderLifecycleError("observations must be a mapping")
    if not isinstance(root_identifier, str) or not root_identifier.strip():
        raise OrderLifecycleError("a root identifier is required")
    if root_identifier not in observations:
        return ChainResult(ChainOutcome.MISSING_PARENT)

    # A parent superseded by two children cannot be resolved: which one carries
    # the intent is not knowable from here.
    children: dict[str, list[str]] = {}
    for identifier, observation in observations.items():
        parent = observation.replaces_identifier
        if parent is None:
            continue
        if parent not in observations:
            return ChainResult(ChainOutcome.MISSING_PARENT)
        children.setdefault(parent, []).append(identifier)
    for successors in children.values():
        if len(successors) > 1:
            return ChainResult(ChainOutcome.DUPLICATE_CHILD)

    seen: set[str] = set()
    current = root_identifier
    depth = 0
    while True:
        if current in seen:
            return ChainResult(ChainOutcome.CYCLE_DETECTED, depth)
        seen.add(current)
        successors = children.get(current, [])
        if not successors:
            state = observations[current].state
            return ChainResult(
                ChainOutcome.NO_REPLACEMENT if depth == 0 else ChainOutcome.RESOLVED,
                depth,
                state,
            )
        parent_state = observations[current].state
        if parent_state not in REPLACEABLE_STATES and parent_state is not (
            LifecycleState.REPLACED
        ):
            return ChainResult(ChainOutcome.INVALID_PARENT_STATE, depth)
        depth += 1
        if depth > maximum_depth:
            return ChainResult(ChainOutcome.EXCESSIVE_DEPTH, depth)
        current = successors[0]


__all__ = [
    "ABSENT_STATES",
    "ALLOWED_TRANSITIONS",
    "BACKWARD_TRANSITIONS",
    "BENIGN_OUTCOMES",
    "LABEL_MAPPINGS",
    "LIFECYCLE_STATES_APPROVED_FOR_PROTECTION",
    "MAXIMUM_REPLACEMENT_DEPTH",
    "PROTECTION_EXPLANATION",
    "PROVENANCE_SUFFICIENT_FOR_PROTECTION",
    "REPLACEABLE_STATES",
    "TERMINAL_STATES",
    "ChainOutcome",
    "ChainResult",
    "LabelMapping",
    "LifecycleState",
    "MappingProvenance",
    "OrderLifecycleError",
    "OrderObservation",
    "ProtectionVerdict",
    "ProtectionVerdictReason",
    "ReconciliationOutcome",
    "ReconciliationResult",
    "assess_protection",
    "label_provenance",
    "normalize_label",
    "reconcile",
    "resolve_replacement_chain",
    "state_provenance",
    "transition_is_allowed",
    "transition_is_backward",
]
