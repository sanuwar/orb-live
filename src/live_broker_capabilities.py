"""
Grounded live broker capability model (OFFLINE, NON-BINDING).

This module is a pure, additive description of what the broker's equity-order
surface can and cannot do, as established by Checkpoint 7A (advertised input
schemas), Checkpoint 7B (observed live response shapes), and the official
tools/list schema snapshot the operator later supplied as an untracked
external reference (advertised INPUT and OUTPUT schemas).

It performs NO broker, MCP, network, credential, token, or database access.
It constructs no transport, no client, and no executor. It invokes no tool.
Nothing here binds the simulated-only SagaExecutor guard, which is unchanged.

Terminology is deliberate and must not be blurred:

  SCHEMA_ADVERTISED  the broker's published schema says so; NOT verified by
                     an observed live response.
  LIVE_OBSERVED      directly observed in a live response during an attended
                     checkpoint.
  UNVERIFIED         neither observed nor establishable from local evidence.

An advertised capability is never described as verified.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping


# --------------------------------------------------------------------------
# Errors. Every unsupported operation fails closed with a typed error.
# --------------------------------------------------------------------------

class LiveCapabilityError(RuntimeError):
    """Base class for every live-capability violation."""


class UnsupportedCapabilityError(LiveCapabilityError):
    """A capability that is not explicitly supported was requested."""


class LookupByRefUnsupportedError(UnsupportedCapabilityError):
    """
    Lookup of an order by client ref_id is not available.

    No advertised tool filters by ref_id, and Checkpoint 7B observed no
    ref_id field in any sampled order record. This operation must fail
    closed; it must NEVER be emulated by scanning unrelated fields.
    """


class OrderReplacementUnsupportedError(UnsupportedCapabilityError):
    """No replace/modify order tool is advertised."""


class BracketOrderUnsupportedError(UnsupportedCapabilityError):
    """No bracket / attached-protective-stop submission is advertised."""


class OcoUnsupportedError(UnsupportedCapabilityError):
    """No one-cancels-other submission is advertised."""


class AtomicStopReplacementUnsupportedError(UnsupportedCapabilityError):
    """
    Atomic protective-stop replacement is not available.

    Replacement would require either cancel-then-place (leaving an
    unprotected interval) or place-then-cancel (risking a double sell).
    """


# --------------------------------------------------------------------------
# Evidence classification.
# --------------------------------------------------------------------------

class CapabilityEvidence(str, Enum):
    """How a capability claim is grounded."""

    SCHEMA_ADVERTISED = "schema_advertised"
    LIVE_OBSERVED = "live_observed"
    UNVERIFIED = "unverified"


@dataclass(frozen=True)
class CapabilityFact:
    """
    One capability claim and its grounding.

    ``supported`` states whether the operation may be attempted at all.
    ``evidence`` states how that is known. A supported capability whose
    evidence is SCHEMA_ADVERTISED has NOT been verified live.
    """

    name: str
    supported: bool
    evidence: CapabilityEvidence
    note: str

    @property
    def is_verified_live(self) -> bool:
        return self.evidence is CapabilityEvidence.LIVE_OBSERVED


# --------------------------------------------------------------------------
# The capability model.
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class LiveBrokerCapabilities:
    """
    Immutable capability model for the grounded equity-order surface.

    Every flag defaults to the grounded value. Construction is explicit so a
    future, differently-capable surface can be represented without editing
    call sites, and so tests can assert fail-closed behaviour.
    """

    supports_non_committing_review: bool = True
    supports_submit_with_ref_id: bool = True
    supports_cancel_by_order_id: bool = True
    cancel_is_asynchronous: bool = True
    supports_order_status_read: bool = True
    supports_position_snapshot: bool = True
    supports_lookup_by_ref: bool = False
    supports_replace: bool = False
    supports_bracket_order: bool = False
    supports_oco: bool = False
    supports_attached_protective_stop: bool = False

    def as_mapping(self) -> Mapping[str, bool]:
        """Flag name -> supported. Ordering is stable."""
        return {
            "supports_non_committing_review": (
                self.supports_non_committing_review
            ),
            "supports_submit_with_ref_id": self.supports_submit_with_ref_id,
            "supports_cancel_by_order_id": self.supports_cancel_by_order_id,
            "cancel_is_asynchronous": self.cancel_is_asynchronous,
            "supports_order_status_read": self.supports_order_status_read,
            "supports_position_snapshot": self.supports_position_snapshot,
            "supports_lookup_by_ref": self.supports_lookup_by_ref,
            "supports_replace": self.supports_replace,
            "supports_bracket_order": self.supports_bracket_order,
            "supports_oco": self.supports_oco,
            "supports_attached_protective_stop": (
                self.supports_attached_protective_stop
            ),
        }

    def supports(self, capability: str) -> bool:
        """
        Whether ``capability`` is explicitly supported.

        An unknown capability name is NOT supported (fail closed).
        """
        return bool(self.as_mapping().get(capability, False))

    def require(self, capability: str) -> None:
        """
        Fail closed unless ``capability`` is explicitly supported.

        Raises the most specific typed error available so callers can
        distinguish the reason without string matching.
        """
        if self.supports(capability):
            return

        specific = _SPECIFIC_ERRORS.get(capability)
        if specific is not None:
            raise specific(_UNSUPPORTED_MESSAGES[capability])

        raise UnsupportedCapabilityError(
            f"Capability {capability!r} is not explicitly supported; "
            "failing closed."
        )


# Grounding for each flag, kept separate from the flags themselves so an
# advertised-but-unverified capability can never be read as verified.
EQUITY_ORDER_CAPABILITY_EVIDENCE: tuple[CapabilityFact, ...] = (
    CapabilityFact(
        name="supports_non_committing_review",
        supported=True,
        evidence=CapabilityEvidence.LIVE_OBSERVED,
        note=(
            "review_equity_order returned a response during Checkpoint 7B "
            "without creating an order."
        ),
    ),
    CapabilityFact(
        name="supports_submit_with_ref_id",
        supported=True,
        evidence=CapabilityEvidence.SCHEMA_ADVERTISED,
        note=(
            "place_equity_order advertises a ref_id idempotency key. The "
            "server-side deduplication behaviour is ADVERTISED ONLY and has "
            "not been verified by any observed response."
        ),
    ),
    CapabilityFact(
        name="supports_cancel_by_order_id",
        supported=True,
        evidence=CapabilityEvidence.SCHEMA_ADVERTISED,
        note=(
            "cancel_equity_order advertises account_number + order_id in, "
            "and a single boolean acceptance out. The response contract is "
            "now schema-advertised, but no cancellation response has been "
            "observed live."
        ),
    ),
    CapabilityFact(
        name="cancel_is_asynchronous",
        supported=True,
        evidence=CapabilityEvidence.SCHEMA_ADVERTISED,
        note=(
            "The advertised output states that acceptance is not "
            "completion and directs the caller to an order-status read for "
            "the final state. Cancellation may also be refused outright. "
            "Final state must be confirmed by an order-status read."
        ),
    ),
    CapabilityFact(
        name="supports_order_status_read",
        supported=True,
        evidence=CapabilityEvidence.LIVE_OBSERVED,
        note=(
            "get_equity_orders returned order records during Checkpoint 7B. "
            "Its full output contract, including the lifecycle state set, "
            "is separately schema-advertised."
        ),
    ),
    CapabilityFact(
        name="supports_position_snapshot",
        supported=True,
        evidence=CapabilityEvidence.SCHEMA_ADVERTISED,
        note=(
            "get_equity_positions is on the commissioned read-only "
            "allowlist and its output contract is schema-advertised. It was "
            "not exercised by Checkpoint 7B."
        ),
    ),
    CapabilityFact(
        name="supports_lookup_by_ref",
        supported=False,
        evidence=CapabilityEvidence.LIVE_OBSERVED,
        note=(
            "No advertised tool filters by ref_id, and no ref_id field was "
            "present in any sampled order record. Strong observed evidence "
            "for the sampled set; not proof about every possible record."
        ),
    ),
    CapabilityFact(
        name="supports_replace",
        supported=False,
        evidence=CapabilityEvidence.SCHEMA_ADVERTISED,
        note="No replace or modify order tool is advertised.",
    ),
    CapabilityFact(
        name="supports_bracket_order",
        supported=False,
        evidence=CapabilityEvidence.SCHEMA_ADVERTISED,
        note=(
            "No bracket tool is advertised and the place schema declares "
            "additionalProperties false with no attached-leg parameter."
        ),
    ),
    CapabilityFact(
        name="supports_oco",
        supported=False,
        evidence=CapabilityEvidence.SCHEMA_ADVERTISED,
        note="No one-cancels-other submission is advertised.",
    ),
    CapabilityFact(
        name="supports_attached_protective_stop",
        supported=False,
        evidence=CapabilityEvidence.SCHEMA_ADVERTISED,
        note=(
            "A protective stop must be a separate order; it cannot be "
            "attached to the entry submission."
        ),
    ),
)


# Response-contract grounding, kept SEPARATE from the capability flags.
#
# These four facts moved from "schema unavailable" to "schema advertised"
# when the operator supplied the official tools/list snapshot. None of them
# is live-verified: a published output schema describes what the broker says
# it returns, and no place or cancel response has ever been observed. They
# change no capability flag; every unsupported flag above is unchanged.
RESPONSE_CONTRACT_EVIDENCE: tuple[CapabilityFact, ...] = (
    CapabilityFact(
        name="place_response_returns_submitted_order",
        supported=True,
        evidence=CapabilityEvidence.SCHEMA_ADVERTISED,
        note=(
            "The place output advertises data.order as the newly created "
            "order, in the same shape as the order-status output. It "
            "establishes SUBMISSION only; it is not evidence of a fill."
        ),
    ),
    CapabilityFact(
        name="cancel_response_returns_async_acceptance",
        supported=True,
        evidence=CapabilityEvidence.SCHEMA_ADVERTISED,
        note=(
            "The cancel output advertises data.accepted as a boolean "
            "describing acceptance of the cancel REQUEST. It is never "
            "evidence of a completed cancellation."
        ),
    ),
    CapabilityFact(
        name="order_status_lifecycle_state_shape",
        supported=True,
        evidence=CapabilityEvidence.SCHEMA_ADVERTISED,
        note=(
            "The order-status output advertises a lifecycle state set that "
            "is strictly larger than the input state filter, and includes "
            "pending_cancelled and partially_filled_rest_cancelled. Which "
            "of these states constitutes active protective coverage is NOT "
            "advertised and remains ungrounded."
        ),
    ),
    CapabilityFact(
        name="position_response_contract",
        supported=True,
        evidence=CapabilityEvidence.SCHEMA_ADVERTISED,
        note=(
            "The position output advertises per-symbol quantity, "
            "sellable-share, and hold-breakdown fields. It has not been "
            "exercised live."
        ),
    ),
)


# The grounded surface. Immutable and shared.
EQUITY_ORDER_CAPABILITIES = LiveBrokerCapabilities()


_SPECIFIC_ERRORS: Mapping[str, type[UnsupportedCapabilityError]] = {
    "supports_lookup_by_ref": LookupByRefUnsupportedError,
    "supports_replace": OrderReplacementUnsupportedError,
    "supports_bracket_order": BracketOrderUnsupportedError,
    "supports_oco": OcoUnsupportedError,
    "supports_attached_protective_stop": (
        AtomicStopReplacementUnsupportedError
    ),
}

_UNSUPPORTED_MESSAGES: Mapping[str, str] = {
    "supports_lookup_by_ref": (
        "Lookup by client ref_id is not supported by this broker surface. "
        "It must not be emulated by scanning unrelated fields."
    ),
    "supports_replace": (
        "Order replacement is not supported by this broker surface."
    ),
    "supports_bracket_order": (
        "Bracket / attached-protective-stop submission is not supported by "
        "this broker surface."
    ),
    "supports_oco": (
        "One-cancels-other submission is not supported by this broker "
        "surface."
    ),
    "supports_attached_protective_stop": (
        "Atomic protective-stop replacement is not supported by this "
        "broker surface."
    ),
}


def capability_evidence(
    name: str,
    facts: tuple[CapabilityFact, ...] = EQUITY_ORDER_CAPABILITY_EVIDENCE,
) -> CapabilityFact:
    """Return the grounding for one capability, or fail closed."""
    for fact in facts:
        if fact.name == name:
            return fact

    raise UnsupportedCapabilityError(
        f"No recorded evidence for capability {name!r}; failing closed."
    )


def unverified_capabilities(
    facts: tuple[CapabilityFact, ...] = EQUITY_ORDER_CAPABILITY_EVIDENCE,
) -> tuple[str, ...]:
    """
    Supported capabilities that are ADVERTISED but not observed live.

    These must never be described as verified.
    """
    return tuple(
        fact.name
        for fact in facts
        if fact.supported and not fact.is_verified_live
    )


# --------------------------------------------------------------------------
# Absolute protection-invariant assessment (pure; executes nothing).
# --------------------------------------------------------------------------

# The approved invariant, quoted verbatim. This module reports whether the
# invariant is technically satisfiable on a given surface. It does NOT
# weaken, rewrite, reinterpret, or amend it.
APPROVED_PROTECTION_INVARIANT = (
    "No filled position may exist without verified protective-stop "
    "coverage for its exact filled quantity."
)


class ProtectionGapReason(str, Enum):
    """Why the absolute protection invariant cannot be satisfied."""

    ENTRY_TO_STOP_GAP = "ENTRY_TO_STOP_GAP"
    CANCEL_THEN_EXIT_GAP = "CANCEL_THEN_EXIT_GAP"
    EXIT_WITH_STOP_DOUBLE_SELL_RISK = "EXIT_WITH_STOP_DOUBLE_SELL_RISK"
    STOP_REPLACEMENT_GAP = "STOP_REPLACEMENT_GAP"


@dataclass(frozen=True)
class ProtectionAssessment:
    """
    Result of assessing the approved invariant against a capability surface.

    This is a report, not a decision. It authorizes nothing, executes
    nothing, and implements no emergency behaviour.
    """

    invariant: str
    technically_satisfiable: bool
    reasons: tuple[ProtectionGapReason, ...]

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return tuple(reason.value for reason in self.reasons)


def assess_absolute_protection_invariant(
    capabilities: LiveBrokerCapabilities = EQUITY_ORDER_CAPABILITIES,
) -> ProtectionAssessment:
    """
    Report whether the approved absolute protection invariant is technically
    satisfiable on ``capabilities``.

    The invariant is reported UNSATISFIED when a protective stop cannot be
    attached to the entry, when replacement is not atomic, and when exit and
    stop must be separate orders. No mitigation, emergency action, or
    reinterpretation is performed here.
    """
    reasons: list[ProtectionGapReason] = []

    attached = (
        capabilities.supports_attached_protective_stop
        or capabilities.supports_bracket_order
    )
    if not attached:
        # A fill necessarily precedes protective-stop confirmation.
        reasons.append(ProtectionGapReason.ENTRY_TO_STOP_GAP)

    if not capabilities.supports_oco:
        # Exit and stop are separate orders with no mutual cancellation.
        reasons.append(ProtectionGapReason.CANCEL_THEN_EXIT_GAP)
        reasons.append(
            ProtectionGapReason.EXIT_WITH_STOP_DOUBLE_SELL_RISK
        )

    if not capabilities.supports_replace:
        reasons.append(ProtectionGapReason.STOP_REPLACEMENT_GAP)

    return ProtectionAssessment(
        invariant=APPROVED_PROTECTION_INVARIANT,
        technically_satisfiable=not reasons,
        reasons=tuple(reasons),
    )
