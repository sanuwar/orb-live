"""Read-only broker-state grounding for unattended operation (Milestone 9).

Nothing in this module performs I/O. It contains no broker call, no session,
no network access, and no order placement, cancellation, replacement, or
modification. It consumes already-read, already-sanitized facts and returns a
decision.

The problem it exists to solve: attended commissioning could rely on a human
watching a single share. Unattended operation cannot. Before a stop may be
trusted without supervision, the runtime must be able to say *this state means
the stop is armed and resting at the venue* — and today it cannot, because
``ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES`` is empty. This module supplies
the framework for grounding that claim without letting it be assumed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from src.live_order_state_model import (
    ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES,
    OrderStateClass,
    classify_order_state,
    normalize_state,
)


class ReadOnlyGroundingError(RuntimeError):
    """A grounding input or policy was invalid. Never a broker condition."""


# --------------------------------------------------------------------------
# Provenance: how much is actually known about a candidate broker state.
# --------------------------------------------------------------------------


class StateProvenance(str, Enum):
    """Where knowledge of a broker state came from.

    The ordering is deliberate and is the whole point of this module. Seeing a
    state in a schema is not seeing it happen. Seeing it happen once is not
    understanding what it means. Only APPROVED_FOR_ACTIVE_PROTECTION may be
    relied upon to assert that a stop is armed, and only a reviewed read-only
    observation may promote a state to it.
    """

    SCHEMA_ONLY = "SCHEMA_ONLY"
    FAKE_ONLY = "FAKE_ONLY"
    HISTORICAL_SANITIZED_EVIDENCE = "HISTORICAL_SANITIZED_EVIDENCE"
    READ_ONLY_LIVE_OBSERVATION = "READ_ONLY_LIVE_OBSERVATION"
    APPROVED_FOR_ACTIVE_PROTECTION = "APPROVED_FOR_ACTIVE_PROTECTION"


#: Provenance levels that are explicitly NOT sufficient to assert protection.
UNGROUNDED_PROVENANCE: frozenset[StateProvenance] = frozenset({
    StateProvenance.SCHEMA_ONLY,
    StateProvenance.FAKE_ONLY,
    StateProvenance.HISTORICAL_SANITIZED_EVIDENCE,
    StateProvenance.READ_ONLY_LIVE_OBSERVATION,
})


@dataclass(frozen=True)
class ProtectionStateCandidate:
    """One broker order state considered for protective meaning.

    ``can_protect`` and ``can_coexist_with_executable_sell`` are recorded as
    ``None`` while unknown. They are answers a later reviewed observation must
    supply; leaving them unset is honest, and guessing them is the failure
    this module is designed to prevent.
    """

    normalized_state: str
    provenance: StateProvenance
    can_protect: bool | None = None
    can_coexist_with_executable_sell: bool | None = None
    note: str = ""

    def __post_init__(self) -> None:
        if self.normalized_state != normalize_state(self.normalized_state):
            raise ReadOnlyGroundingError(
                "candidate state must already be normalized"
            )
        if not self.normalized_state:
            raise ReadOnlyGroundingError("candidate state must not be blank")
        if (
            self.provenance is StateProvenance.APPROVED_FOR_ACTIVE_PROTECTION
            and not self.can_protect
        ):
            raise ReadOnlyGroundingError(
                "an approved state must be known to protect"
            )

    @property
    def order_state_class(self) -> OrderStateClass:
        return classify_order_state(self.normalized_state)

    @property
    def is_terminal(self) -> bool:
        return self.order_state_class in {
            OrderStateClass.TERMINAL_CANCELLED,
            OrderStateClass.TERMINAL_FILLED,
            OrderStateClass.TERMINAL_REJECTED_OR_FAILED,
            OrderStateClass.PARTIAL_FILL_WITH_REMAINDER_CANCELLED,
        }

    @property
    def is_executable(self) -> bool:
        """Whether an order in this state may still transact.

        Cancellation-pending counts as executable: a cancel that has not
        settled can still be beaten by a fill.
        """
        return self.order_state_class in {
            OrderStateClass.ACTIVE_OR_WORKING,
            OrderStateClass.CANCELLATION_PENDING,
        }

    @property
    def is_approved_for_active_protection(self) -> bool:
        return (
            self.provenance is StateProvenance.APPROVED_FOR_ACTIVE_PROTECTION
        )


def _candidate(
    state: str,
    provenance: StateProvenance,
    note: str = "",
) -> tuple[str, ProtectionStateCandidate]:
    return state, ProtectionStateCandidate(
        normalized_state=state, provenance=provenance, note=note
    )


# Every state this repository has any knowledge of, with the honest source of
# that knowledge. `confirmed`, `unconfirmed`, `filled`, and `cancelled` were
# actually observed in sanitized evidence from completed attended runs — that
# is strictly more than schema knowledge and still strictly less than approval.
# NOTHING here is approved for active protection.
PROTECTION_STATE_CANDIDATES: Mapping[str, ProtectionStateCandidate] = (
    MappingProxyType(dict([
        _candidate(
            "confirmed", StateProvenance.HISTORICAL_SANITIZED_EVIDENCE,
            "Observed as a resting protective stop in completed attended runs. "
            "Whether it guarantees the stop is armed at the venue is unproven.",
        ),
        _candidate(
            "unconfirmed", StateProvenance.HISTORICAL_SANITIZED_EVIDENCE,
            "Observed immediately after stop placement. Very likely NOT yet "
            "working at the venue.",
        ),
        _candidate(
            "filled", StateProvenance.HISTORICAL_SANITIZED_EVIDENCE,
            "Terminal. A filled stop protects nothing further.",
        ),
        _candidate(
            "cancelled", StateProvenance.HISTORICAL_SANITIZED_EVIDENCE,
            "Terminal. Observed after controlled flatten.",
        ),
        _candidate("new", StateProvenance.SCHEMA_ONLY),
        _candidate("queued", StateProvenance.SCHEMA_ONLY),
        _candidate("partially_filled", StateProvenance.SCHEMA_ONLY),
        _candidate("pending_cancelled", StateProvenance.SCHEMA_ONLY),
        _candidate("partially_filled_rest_cancelled", StateProvenance.SCHEMA_ONLY),
        _candidate("rejected", StateProvenance.SCHEMA_ONLY),
        _candidate("failed", StateProvenance.SCHEMA_ONLY),
        _candidate("voided", StateProvenance.SCHEMA_ONLY),
        _candidate("locating", StateProvenance.SCHEMA_ONLY),
        _candidate("locate_failed", StateProvenance.SCHEMA_ONLY),
    ]))
)


def approved_active_protection_states() -> frozenset[str]:
    """The states approved to assert active protection. Currently empty.

    Derived from the candidate registry rather than written twice, so the
    approved set cannot drift from its provenance record.
    """
    return frozenset(
        state
        for state, candidate in PROTECTION_STATE_CANDIDATES.items()
        if candidate.is_approved_for_active_protection
    )


def grounding_is_complete() -> bool:
    """Whether any state has been approved. False until a reviewed observation."""
    return bool(approved_active_protection_states())


# --------------------------------------------------------------------------
# Snapshot classifications. Fixed vocabulary only; never a raw broker value.
# --------------------------------------------------------------------------


class ReadAvailability(str, Enum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_ATTEMPTED = "NOT_ATTEMPTED"


class CashAvailability(str, Enum):
    SUFFICIENT_FOR_PLANNED_RISK = "SUFFICIENT_FOR_PLANNED_RISK"
    INSUFFICIENT = "INSUFFICIENT"
    UNKNOWN = "UNKNOWN"


class PositionClassification(str, Enum):
    FLAT = "FLAT"
    LONG_EXPECTED_QUANTITY = "LONG_EXPECTED_QUANTITY"
    LONG_UNEXPECTED_QUANTITY = "LONG_UNEXPECTED_QUANTITY"
    SHORT = "SHORT"
    CONTRADICTORY = "CONTRADICTORY"
    UNKNOWN = "UNKNOWN"


class ProtectionObservation(str, Enum):
    NOT_OBSERVED = "NOT_OBSERVED"
    OBSERVED_APPROVED_STATE = "OBSERVED_APPROVED_STATE"
    OBSERVED_UNAPPROVED_STATE = "OBSERVED_UNAPPROVED_STATE"
    OBSERVED_TERMINAL = "OBSERVED_TERMINAL"
    OBSERVED_UNKNOWN_STATE = "OBSERVED_UNKNOWN_STATE"
    AMBIGUOUS_CANDIDATES = "AMBIGUOUS_CANDIDATES"


class MarketSession(str, Enum):
    REGULAR_HOURS = "REGULAR_HOURS"
    CLOSED = "CLOSED"
    EXTENDED_HOURS = "EXTENDED_HOURS"
    CHANGED_DURING_ACQUISITION = "CHANGED_DURING_ACQUISITION"
    UNKNOWN = "UNKNOWN"


class SnapshotFreshness(str, Enum):
    FRESH = "FRESH"
    STALE = "STALE"
    SPAN_TOO_WIDE = "SPAN_TOO_WIDE"
    UNKNOWN = "UNKNOWN"


class BrokerStateDecision(str, Enum):
    """The only conclusions a caller may act on. Every one is explicit."""

    VERIFIED_FLAT = "VERIFIED_FLAT"
    VERIFIED_ACTIVE_PROTECTION = "VERIFIED_ACTIVE_PROTECTION"
    UNPROTECTED_POSITION = "UNPROTECTED_POSITION"
    CONFLICTING_EXECUTABLE_ORDERS = "CONFLICTING_EXECUTABLE_ORDERS"
    STALE_BROKER_STATE = "STALE_BROKER_STATE"
    INCOMPLETE_BROKER_STATE = "INCOMPLETE_BROKER_STATE"
    UNKNOWN_BROKER_STATE = "UNKNOWN_BROKER_STATE"
    BROKER_READ_UNAVAILABLE = "BROKER_READ_UNAVAILABLE"


#: Decisions that permit an automated caller to proceed. Everything else must
#: suspend trading and require an operator.
SAFE_DECISIONS: frozenset[BrokerStateDecision] = frozenset({
    BrokerStateDecision.VERIFIED_FLAT,
    BrokerStateDecision.VERIFIED_ACTIVE_PROTECTION,
})


# --------------------------------------------------------------------------
# Freshness policy.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SnapshotFreshnessPolicy:
    """How old a snapshot may be, and how far apart its reads may spread.

    The defaults are deliberately tight. A decision cycle that cannot gather
    account, position, order, and session state within a couple of seconds is
    not observing one coherent moment, and an unattended system acting on a
    smeared snapshot is acting on a state that never existed.
    """

    maximum_age_seconds: float = 5.0
    maximum_span_seconds: float = 2.0

    def __post_init__(self) -> None:
        for name in ("maximum_age_seconds", "maximum_span_seconds"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ReadOnlyGroundingError(f"{name} must be a number")
            if value <= 0:
                raise ReadOnlyGroundingError(f"{name} must be positive")
            if value > 60:
                raise ReadOnlyGroundingError(
                    f"{name} above 60s cannot describe one decision cycle"
                )
        if self.maximum_span_seconds > self.maximum_age_seconds:
            raise ReadOnlyGroundingError(
                "read span cannot exceed the maximum snapshot age"
            )


DEFAULT_FRESHNESS_POLICY = SnapshotFreshnessPolicy()


# --------------------------------------------------------------------------
# The snapshot.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ReadOnlyBrokerSnapshot:
    """One decision cycle's sanitized view of broker state.

    Every field is a fixed classification, a count, or a monotonic offset.
    No account number, buying-power amount, balance, order identifier, broker
    symbol, price, token, header, or response body may be represented here —
    there is no field capable of carrying one.
    """

    account_read: ReadAvailability = ReadAvailability.NOT_ATTEMPTED
    position_read: ReadAvailability = ReadAvailability.NOT_ATTEMPTED
    order_read: ReadAvailability = ReadAvailability.NOT_ATTEMPTED
    session_read: ReadAvailability = ReadAvailability.NOT_ATTEMPTED

    cash_availability: CashAvailability = CashAvailability.UNKNOWN
    position: PositionClassification = PositionClassification.UNKNOWN
    market_session: MarketSession = MarketSession.UNKNOWN

    related_open_order_count: int = 0
    executable_related_sell_count: int = 0
    protective_candidate_count: int = 0
    protection: ProtectionObservation = ProtectionObservation.NOT_OBSERVED

    #: Monotonic seconds, oldest and newest read in this cycle, and evaluation.
    first_read_monotonic: float | None = None
    last_read_monotonic: float | None = None
    evaluated_monotonic: float | None = None

    order_state_changed_during_acquisition: bool = False
    position_observations_disagreed: bool = False

    def __post_init__(self) -> None:
        for name in (
            "related_open_order_count",
            "executable_related_sell_count",
            "protective_candidate_count",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise ReadOnlyGroundingError(f"{name} must be an int")
            if value < 0:
                raise ReadOnlyGroundingError(f"{name} must not be negative")

    @property
    def required_reads(self) -> tuple[ReadAvailability, ...]:
        return (
            self.account_read,
            self.position_read,
            self.order_read,
            self.session_read,
        )

    @property
    def is_complete(self) -> bool:
        return all(
            read is ReadAvailability.AVAILABLE for read in self.required_reads
        )

    def freshness(
        self, policy: SnapshotFreshnessPolicy = DEFAULT_FRESHNESS_POLICY
    ) -> SnapshotFreshness:
        stamps = (
            self.first_read_monotonic,
            self.last_read_monotonic,
            self.evaluated_monotonic,
        )
        if any(stamp is None for stamp in stamps):
            return SnapshotFreshness.UNKNOWN
        first, last, evaluated = stamps  # type: ignore[misc]
        if last < first or evaluated < last:
            # Time did not move forward across the cycle; nothing about this
            # snapshot's ordering can be trusted.
            return SnapshotFreshness.UNKNOWN
        if last - first > policy.maximum_span_seconds:
            return SnapshotFreshness.SPAN_TOO_WIDE
        if evaluated - first > policy.maximum_age_seconds:
            return SnapshotFreshness.STALE
        return SnapshotFreshness.FRESH

    def sanitized_facts(self) -> dict[str, str]:
        """Allowlisted evidence fields for this snapshot. Counts become buckets."""
        return {
            "account_read": self.account_read.value,
            "position_read": self.position_read.value,
            "order_read": self.order_read.value,
            "session_read": self.session_read.value,
            "cash_availability": self.cash_availability.value,
            "position_classification": self.position.value,
            "market_session": self.market_session.value,
            "protection_observation": self.protection.value,
            "related_open_orders": count_bucket(self.related_open_order_count),
            "executable_related_sells": count_bucket(
                self.executable_related_sell_count
            ),
            "protective_candidates": count_bucket(
                self.protective_candidate_count
            ),
            "order_state_changed_during_acquisition": _flag(
                self.order_state_changed_during_acquisition
            ),
            "position_observations_disagreed": _flag(
                self.position_observations_disagreed
            ),
        }


COUNT_BUCKETS: frozenset[str] = frozenset({"none", "one", "many"})


def count_bucket(value: int) -> str:
    """Coarse count. An exact tally of broker orders is not decision-relevant."""
    if value <= 0:
        return "none"
    if value == 1:
        return "one"
    return "many"


def _flag(value: bool) -> str:
    return "true" if value else "false"


#: Allowlist for the snapshot's evidence fields: key -> permitted values.
ALLOWED_SNAPSHOT_FACTS: Mapping[str, frozenset[str]] = MappingProxyType({
    "account_read": frozenset(item.value for item in ReadAvailability),
    "position_read": frozenset(item.value for item in ReadAvailability),
    "order_read": frozenset(item.value for item in ReadAvailability),
    "session_read": frozenset(item.value for item in ReadAvailability),
    "cash_availability": frozenset(item.value for item in CashAvailability),
    "position_classification": frozenset(
        item.value for item in PositionClassification
    ),
    "market_session": frozenset(item.value for item in MarketSession),
    "protection_observation": frozenset(
        item.value for item in ProtectionObservation
    ),
    "related_open_orders": COUNT_BUCKETS,
    "executable_related_sells": COUNT_BUCKETS,
    "protective_candidates": COUNT_BUCKETS,
    "order_state_changed_during_acquisition": frozenset({"true", "false"}),
    "position_observations_disagreed": frozenset({"true", "false"}),
    "broker_state_decision": frozenset(
        item.value for item in BrokerStateDecision
    ),
    "snapshot_freshness": frozenset(item.value for item in SnapshotFreshness),
    "active_protection_grounding": frozenset({"complete", "incomplete"}),
})


def validate_snapshot_facts(facts: Mapping[str, str]) -> None:
    """Reject any key or value outside the allowlist."""
    if not isinstance(facts, Mapping):
        raise ReadOnlyGroundingError("snapshot facts must be a mapping")
    for key, value in facts.items():
        allowed = ALLOWED_SNAPSHOT_FACTS.get(key)
        if allowed is None:
            raise ReadOnlyGroundingError("snapshot fact key is invalid")
        if not isinstance(value, str) or value not in allowed:
            raise ReadOnlyGroundingError("snapshot fact value is invalid")


# --------------------------------------------------------------------------
# The decision.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class BrokerStateAssessment:
    decision: BrokerStateDecision
    freshness: SnapshotFreshness
    grounding_complete: bool
    facts: Mapping[str, str] = field(default_factory=dict)

    @property
    def is_safe_to_proceed(self) -> bool:
        return self.decision in SAFE_DECISIONS


def decide_broker_state(
    snapshot: ReadOnlyBrokerSnapshot,
    *,
    policy: SnapshotFreshnessPolicy = DEFAULT_FRESHNESS_POLICY,
    approved_states: frozenset[str] | None = None,
) -> BrokerStateAssessment:
    """Reduce one sanitized snapshot to one fail-closed decision.

    Pure: performs no I/O, opens no session, and touches no broker. It cannot
    place, cancel, replace, or modify anything.

    Order of evaluation is itself a safety property. Availability, then
    completeness, then freshness, then internal contradiction are all checked
    *before* anything is interpreted, because a conclusion drawn from an
    unavailable, partial, stale, or self-contradictory snapshot is worse than
    no conclusion.
    """
    if not isinstance(snapshot, ReadOnlyBrokerSnapshot):
        raise ReadOnlyGroundingError("a ReadOnlyBrokerSnapshot is required")

    if approved_states is None:
        approved_states = approved_active_protection_states()
    grounded = bool(approved_states)
    freshness = snapshot.freshness(policy)

    def result(decision: BrokerStateDecision) -> BrokerStateAssessment:
        facts = dict(snapshot.sanitized_facts())
        facts["broker_state_decision"] = decision.value
        facts["snapshot_freshness"] = freshness.value
        facts["active_protection_grounding"] = (
            "complete" if grounded else "incomplete"
        )
        validate_snapshot_facts(facts)
        return BrokerStateAssessment(
            decision=decision,
            freshness=freshness,
            grounding_complete=grounded,
            facts=MappingProxyType(facts),
        )

    # 1. Could we read at all?
    if any(
        read is ReadAvailability.UNAVAILABLE for read in snapshot.required_reads
    ):
        return result(BrokerStateDecision.BROKER_READ_UNAVAILABLE)

    # 2. Did we read everything required?
    if not snapshot.is_complete:
        return result(BrokerStateDecision.INCOMPLETE_BROKER_STATE)

    # 3. Is what we read recent enough, and gathered close enough together?
    if freshness is not SnapshotFreshness.FRESH:
        if freshness is SnapshotFreshness.UNKNOWN:
            return result(BrokerStateDecision.UNKNOWN_BROKER_STATE)
        return result(BrokerStateDecision.STALE_BROKER_STATE)

    # 4. Did the world move while we were looking at it?
    if (
        snapshot.order_state_changed_during_acquisition
        or snapshot.position_observations_disagreed
        or snapshot.position is PositionClassification.CONTRADICTORY
        or snapshot.market_session is MarketSession.CHANGED_DURING_ACQUISITION
    ):
        return result(BrokerStateDecision.UNKNOWN_BROKER_STATE)

    # 5. Anything we simply do not understand fails closed.
    if (
        snapshot.position is PositionClassification.UNKNOWN
        or snapshot.market_session is MarketSession.UNKNOWN
        or snapshot.protection is ProtectionObservation.OBSERVED_UNKNOWN_STATE
    ):
        return result(BrokerStateDecision.UNKNOWN_BROKER_STATE)

    # 6. More than one thing that can still sell is never safe, flat or not.
    if snapshot.executable_related_sell_count > 1:
        return result(BrokerStateDecision.CONFLICTING_EXECUTABLE_ORDERS)
    if snapshot.protection is ProtectionObservation.AMBIGUOUS_CANDIDATES:
        return result(BrokerStateDecision.CONFLICTING_EXECUTABLE_ORDERS)

    # 7. A short position is outside this system's permitted universe.
    if snapshot.position is PositionClassification.SHORT:
        return result(BrokerStateDecision.UNKNOWN_BROKER_STATE)

    # 8. Flat is only flat when nothing can still sell.
    if snapshot.position is PositionClassification.FLAT:
        if snapshot.executable_related_sell_count > 0:
            return result(BrokerStateDecision.CONFLICTING_EXECUTABLE_ORDERS)
        return result(BrokerStateDecision.VERIFIED_FLAT)

    # 9. An unexpected long quantity is not something to reason further about.
    if snapshot.position is PositionClassification.LONG_UNEXPECTED_QUANTITY:
        return result(BrokerStateDecision.UNKNOWN_BROKER_STATE)

    # 10. A held position: protection must be positively established.
    if snapshot.protection is ProtectionObservation.OBSERVED_APPROVED_STATE:
        if not grounded:
            # Claimed approved while nothing is approved. Refuse rather than
            # trust the claim.
            return result(BrokerStateDecision.UNKNOWN_BROKER_STATE)
        if snapshot.protective_candidate_count != 1:
            return result(BrokerStateDecision.CONFLICTING_EXECUTABLE_ORDERS)
        return result(BrokerStateDecision.VERIFIED_ACTIVE_PROTECTION)

    if snapshot.protection is ProtectionObservation.OBSERVED_UNAPPROVED_STATE:
        # Something is resting there, but nothing grounds what it means.
        return result(BrokerStateDecision.UNKNOWN_BROKER_STATE)

    # Terminal or absent protection over a live position.
    return result(BrokerStateDecision.UNPROTECTED_POSITION)


# --------------------------------------------------------------------------
# The later read-only observation checkpoint: design only, never executed here.
# --------------------------------------------------------------------------


READ_ONLY_CHECKPOINT_TOOLS: tuple[str, ...] = (
    "get_accounts",
    "get_equity_positions",
    "get_equity_orders",
)

#: Tools the checkpoint may never call. Enforced by test, not by comment.
READ_ONLY_CHECKPOINT_PROHIBITED_TOOLS: frozenset[str] = frozenset({
    "place_equity_order",
    "cancel_equity_order",
    "replace_equity_order",
    "place_option_order",
    "cancel_option_order",
})


@dataclass(frozen=True)
class ReadOnlyObservationPlan:
    """The shape of a future attended read-only grounding checkpoint.

    It exists so the constraints are reviewable now, in tracked code, rather
    than argued about later next to a live terminal. This object describes a
    checkpoint; it does not perform one.
    """

    tools: tuple[str, ...] = READ_ONLY_CHECKPOINT_TOOLS
    maximum_calls_per_tool: int = 1
    order_placements: int = 0
    order_cancellations: int = 0
    order_replacements: int = 0
    requires_existing_protective_order: bool = True
    manufactures_observation: bool = False

    def __post_init__(self) -> None:
        if self.order_placements or self.order_cancellations or self.order_replacements:
            raise ReadOnlyGroundingError(
                "the grounding checkpoint is read-only and may not write"
            )
        if self.manufactures_observation:
            raise ReadOnlyGroundingError(
                "an observation may never be manufactured by trading"
            )
        prohibited = set(self.tools) & READ_ONLY_CHECKPOINT_PROHIBITED_TOOLS
        if prohibited:
            raise ReadOnlyGroundingError("a write tool is not read-only")
        if self.maximum_calls_per_tool < 1:
            raise ReadOnlyGroundingError("at least one read is required")


class ObservationOutcome(str, Enum):
    """What a grounding checkpoint may conclude.

    NOT_OBSERVED is a legitimate, expected result. If no safe pre-existing
    protective order happens to exist, the checkpoint reports that and stops.
    It never creates one: a live trade placed to generate an observation would
    be a write, and this milestone performs none.
    """

    NOT_OBSERVED = "NOT_OBSERVED"
    OBSERVED_PENDING_REVIEW = "OBSERVED_PENDING_REVIEW"
    READ_UNAVAILABLE = "READ_UNAVAILABLE"


def proposed_state_promotion(
    outcome: ObservationOutcome,
    normalized_state: str,
) -> StateProvenance:
    """The most a single read-only observation may earn a state.

    Deliberately never returns APPROVED_FOR_ACTIVE_PROTECTION. Promotion to
    approved requires human review of the recorded observations, not the act
    of observing. One sighting is not a contract.
    """
    if outcome is not ObservationOutcome.OBSERVED_PENDING_REVIEW:
        raise ReadOnlyGroundingError("no observation to promote")
    candidate = PROTECTION_STATE_CANDIDATES.get(normalize_state(normalized_state))
    if candidate is None:
        raise ReadOnlyGroundingError("unknown state cannot be promoted")
    return StateProvenance.READ_ONLY_LIVE_OBSERVATION


def grounding_consistency_report() -> dict[str, Any]:
    """Cross-check this module against the adapter mapping it must agree with."""
    derived = approved_active_protection_states()
    return {
        "approved_states": sorted(derived),
        "adapter_states": sorted(ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES),
        "agrees_with_adapter": (
            derived == ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES
        ),
        "grounding_complete": bool(derived),
        "candidate_count": len(PROTECTION_STATE_CANDIDATES),
    }
