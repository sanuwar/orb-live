"""Supervised-execution gate — defined, and deliberately inert.

A gate is the thing that finally says "yes". Every other module in this
programme has been careful to return `permits_automated_action=False`, and
each of them names a gate as the place where authority would come from. This
is that place — and it is **closed**.

## Why it is closed rather than absent

Writing the gate now, closed, is the point. It forces the preconditions to be
enumerated while nobody is under pressure to ship, and it makes the missing
ones visible as data rather than as an argument. `open_gate` cannot return an
open verdict today because `ACTIVATION_APPROVALS` is empty, and a test proves
that no combination of inputs opens it.

## Why it imports nothing

The gate takes a `GatePreconditions` record of already-decided facts rather
than importing the risk, recovery, lifecycle, and monitoring modules and
re-deriving them. Two reasons, both practical:

* those modules live in separate unmerged branches, and importing them here
  would tangle four reviews into one;
* a gate that re-derives its own inputs is a gate that can disagree with the
  modules that produced them, and the disagreement would be invisible.

The caller populates this record. That is deliberate: assembling the evidence
is a visible step somebody has to write, rather than something the gate does
quietly on its own behalf.

## The asymmetry that matters

Every precondition must be **affirmatively true** to open. Unknown is not
neutral — it is closed. There is no field whose absence is treated as
satisfied, and `GatePreconditions` has no default values for exactly that
reason.

## Why a bare `True` is not evidence

An earlier version of this module took nine plain booleans. A caller could
assert all nine and, had the mode ever been approved, the gate would have
opened on nothing but the caller's own say-so. A gate that accepts a
self-report is not gating anything; it is agreeing.

So every precondition is an `Attestation`: a value, **where it came from**,
and **when it was established**. `EvidenceSource.SELF_DECLARED` appears in no
precondition's accepted set, which makes caller-supplied booleans structurally
insufficient rather than merely discouraged. `REQUIRED_EVIDENCE` states per
field what will do — a human decision for things that are decisions, a
measurement for things that are observations, and an exercised test for
`automatic_suspension_armed`, because "armed" asserted by the component that
would have to fire is worth nothing until something has made it fire.

Evidence also expires. "The kill switch was disengaged" is a fact about a
moment, not a standing truth, so an attestation older than
`MAXIMUM_ATTESTATION_AGE_SECONDS` — or dated in the future — fails closed.
`current_monotonic` is a required argument for the same reason: a default
clock would let a caller omit the one input that makes "recently verified"
mean anything.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import Enum
from typing import Mapping


class ExecutionGateError(RuntimeError):
    """A malformed gate input. Never carries broker text."""


class GateVerdict(str, Enum):
    """The gate's answer. `OPEN` is currently unreachable."""

    OPEN = "OPEN"
    CLOSED_ACTIVATION_NOT_APPROVED = "CLOSED_ACTIVATION_NOT_APPROVED"
    CLOSED_PRECONDITION_UNMET = "CLOSED_PRECONDITION_UNMET"
    CLOSED_MALFORMED_INPUT = "CLOSED_MALFORMED_INPUT"


class GateMode(str, Enum):
    """Who is watching."""

    SUPERVISED = "SUPERVISED"
    #: Nobody is watching. Everything the supervised mode requires still
    #: applies, plus the conditions below — see `UNATTENDED_PRECONDITIONS`.
    LIMITED_UNATTENDED = "LIMITED_UNATTENDED"


#: Operator approvals that would permit a gate mode to open. Empty, and a test
#: asserts it. Populating this is an operator decision recorded in a reviewed
#: configuration change -- never a code change made to unblock a test.
ACTIVATION_APPROVALS: frozenset[GateMode] = frozenset()


AUTHORITY_EXPLANATION = (
    "This gate is defined and inert. Opening it requires an operator "
    "approval recorded in ACTIVATION_APPROVALS, which is empty. No "
    "combination of preconditions can open a gate whose mode has not been "
    "approved."
)


class EvidenceSource(str, Enum):
    """Where a precondition's truth actually came from.

    This exists because a bare `True` is not evidence. A caller asserting nine
    booleans is asserting nine things about itself, and a gate that accepts
    them is not gating anything — it is agreeing.
    """

    #: The caller simply says so. **Never sufficient for anything.**
    SELF_DECLARED = "SELF_DECLARED"
    #: A human recorded it in a reviewed artifact.
    OPERATOR_ATTESTED = "OPERATOR_ATTESTED"
    #: Produced by a component that observed the thing it reports.
    MEASURED = "MEASURED"
    #: Exercised by a verification that would have failed if untrue.
    TESTED = "TESTED"


@dataclass(frozen=True)
class Attestation:
    """A precondition's value, its provenance, and when it was established."""

    satisfied: bool
    source: EvidenceSource
    recorded_monotonic: float

    def __post_init__(self) -> None:
        if not isinstance(self.satisfied, bool):
            # A truthy non-bool is how "unknown" sneaks in as "yes".
            raise ExecutionGateError("satisfied must be a bool")
        if not isinstance(self.source, EvidenceSource):
            raise ExecutionGateError("evidence source is invalid")
        if not isinstance(self.recorded_monotonic, (int, float)) or isinstance(
            self.recorded_monotonic, bool
        ):
            raise ExecutionGateError("attestation time must be a number")


#: How old an attestation may be. Evidence about a live system decays: "the
#: kill switch was disengaged" is a fact about a moment, not a standing truth.
MAXIMUM_ATTESTATION_AGE_SECONDS = 300.0


#: What each precondition will accept as evidence. `SELF_DECLARED` appears in
#: none of them, which is the point: caller-supplied booleans alone can never
#: satisfy any precondition, whatever the caller believes.
#:
#: The distinctions are deliberate. `strategy_enabled` and
#: `protection_grounding_complete` require a human, because both are decisions
#: rather than observations. `automatic_suspension_armed` requires `TESTED` —
#: "armed" asserted by the thing that would have to fire is worth nothing; it
#: has to have been exercised.
REQUIRED_EVIDENCE: Mapping[str, frozenset[EvidenceSource]] = {
    "operator_present_and_acknowledged": frozenset({EvidenceSource.OPERATOR_ATTESTED}),
    "strategy_enabled": frozenset({EvidenceSource.OPERATOR_ATTESTED}),
    "protection_grounding_complete": frozenset({EvidenceSource.OPERATOR_ATTESTED}),
    "broker_state_verified_fresh": frozenset({EvidenceSource.MEASURED}),
    "recovery_consistent": frozenset({EvidenceSource.MEASURED}),
    "risk_within_limits": frozenset({EvidenceSource.MEASURED}),
    "kill_switch_disengaged": frozenset({EvidenceSource.MEASURED}),
    "monitoring_healthy": frozenset({EvidenceSource.MEASURED, EvidenceSource.TESTED}),
    "market_session_valid": frozenset({EvidenceSource.MEASURED}),
    # --- limited-unattended additions ---
    # Capital, expiry, window and prior-run resolution are operator decisions
    # recorded in reviewed configuration, not things a runtime can observe
    # about itself.
    "capital_tier_approved": frozenset({EvidenceSource.OPERATOR_ATTESTED}),
    "deployment_not_expired": frozenset({EvidenceSource.OPERATOR_ATTESTED}),
    "within_permitted_trading_window": frozenset({EvidenceSource.MEASURED}),
    "no_unresolved_operator_action": frozenset({EvidenceSource.OPERATOR_ATTESTED}),
    "supervised_evidence_reviewed": frozenset({EvidenceSource.OPERATOR_ATTESTED}),
    # These three must have been *exercised*, not merely declared. "Alerting
    # is reachable" and "the kill switch works remotely" and "suspension is
    # armed" are each claims about machinery that has to fire when nobody is
    # watching; a component asserting its own readiness proves nothing.
    "alerting_verified_reachable": frozenset({EvidenceSource.TESTED}),
    "kill_switch_remotely_operable": frozenset({EvidenceSource.TESTED}),
    "automatic_suspension_armed": frozenset({EvidenceSource.TESTED}),
}


def evidence_is_acceptable(name: str, attestation: Attestation) -> bool:
    accepted = REQUIRED_EVIDENCE.get(name)
    if accepted is None:
        return False
    return attestation.source in accepted


@dataclass(frozen=True)
class GatePreconditions:
    """Facts the caller has established elsewhere, each with its provenance.

    Every field is required, and every one must be satisfied **by acceptable
    evidence** and **recently enough**. There are no defaults: a default would
    mean an unstated precondition counts as satisfied, which is the failure
    mode this whole design exists to avoid.
    """

    #: A human is present and has acknowledged this specific session.
    operator_present_and_acknowledged: Attestation
    #: `strategy.enabled` is true in reviewed configuration.
    strategy_enabled: Attestation
    #: At least one protection state has been approved by human review.
    protection_grounding_complete: Attestation
    #: Broker state was read successfully and is fresh.
    broker_state_verified_fresh: Attestation
    #: Restart recovery reported no inconsistency.
    recovery_consistent: Attestation
    #: The proposed action is inside operator-set limits.
    risk_within_limits: Attestation
    #: The kill switch is disengaged.
    kill_switch_disengaged: Attestation
    #: Monitoring is running and its heartbeat is healthy.
    monitoring_healthy: Attestation
    #: The market is in a session where this action is valid.
    market_session_valid: Attestation

    def __post_init__(self) -> None:
        for item in fields(self):
            if not isinstance(getattr(self, item.name), Attestation):
                raise ExecutionGateError(f"{item.name} must be an Attestation")

    def unmet(
        self,
        *,
        current_monotonic: float,
        maximum_age_seconds: float = MAXIMUM_ATTESTATION_AGE_SECONDS,
    ) -> tuple[str, ...]:
        """Names of preconditions that are false, badly evidenced, or stale."""
        failing: list[str] = []
        for item in fields(self):
            attestation: Attestation = getattr(self, item.name)
            age = current_monotonic - attestation.recorded_monotonic
            if (
                attestation.satisfied is not True
                or not evidence_is_acceptable(item.name, attestation)
                or age < 0
                or age > maximum_age_seconds
            ):
                failing.append(item.name)
        return tuple(failing)

    def all_met(
        self,
        *,
        current_monotonic: float,
        maximum_age_seconds: float = MAXIMUM_ATTESTATION_AGE_SECONDS,
    ) -> bool:
        return not self.unmet(
            current_monotonic=current_monotonic, maximum_age_seconds=maximum_age_seconds
        )


#: The precondition names, fixed so a reviewer can see the whole list without
#: reading the dataclass, and so a test can assert none was quietly removed.
REQUIRED_PRECONDITIONS: tuple[str, ...] = (
    "operator_present_and_acknowledged",
    "strategy_enabled",
    "protection_grounding_complete",
    "broker_state_verified_fresh",
    "recovery_consistent",
    "risk_within_limits",
    "kill_switch_disengaged",
    "monitoring_healthy",
    "market_session_valid",
)


# --------------------------------------------------------------------------
# Limited unattended operation.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class UnattendedPreconditions:
    """What unattended operation requires **in addition** to everything above.

    The relationship is strictly additive and enforced by test: unattended can
    only ever require *more* than supervised, never less. A mode that relaxed
    a condition because nobody is watching would have the logic exactly
    backwards — the absence of a human is the reason to require more.

    `operator_present_and_acknowledged` stays in the base set and stays
    required. That is not an oversight. Unattended means no human watches the
    *session*; it does not mean no human ever authorized the deployment. The
    acknowledgement moves earlier in time, it does not disappear.
    """

    #: A capital tier was approved by reviewed configuration, not derived.
    capital_tier_approved: Attestation
    #: The deployment's expiry has not passed.
    deployment_not_expired: Attestation
    #: Trading is inside the approved window for this deployment.
    within_permitted_trading_window: Attestation
    #: Alert delivery was verified reachable *before* the session began.
    alerting_verified_reachable: Attestation
    #: The kill switch can be operated without physical access to the host.
    kill_switch_remotely_operable: Attestation
    #: Automatic suspension is armed and was tested.
    automatic_suspension_armed: Attestation
    #: No prior run ended requiring operator attention.
    no_unresolved_operator_action: Attestation
    #: Supervised runs were reviewed and judged sufficient evidence.
    supervised_evidence_reviewed: Attestation

    def __post_init__(self) -> None:
        for item in fields(self):
            if not isinstance(getattr(self, item.name), Attestation):
                raise ExecutionGateError(f"{item.name} must be an Attestation")

    def unmet(
        self,
        *,
        current_monotonic: float,
        maximum_age_seconds: float = MAXIMUM_ATTESTATION_AGE_SECONDS,
    ) -> tuple[str, ...]:
        failing: list[str] = []
        for item in fields(self):
            attestation: Attestation = getattr(self, item.name)
            age = current_monotonic - attestation.recorded_monotonic
            if (
                attestation.satisfied is not True
                or not evidence_is_acceptable(item.name, attestation)
                or age < 0
                or age > maximum_age_seconds
            ):
                failing.append(item.name)
        return tuple(failing)

    def all_met(
        self,
        *,
        current_monotonic: float,
        maximum_age_seconds: float = MAXIMUM_ATTESTATION_AGE_SECONDS,
    ) -> bool:
        return not self.unmet(
            current_monotonic=current_monotonic, maximum_age_seconds=maximum_age_seconds
        )


UNATTENDED_PRECONDITIONS: tuple[str, ...] = (
    "capital_tier_approved",
    "deployment_not_expired",
    "within_permitted_trading_window",
    "alerting_verified_reachable",
    "kill_switch_remotely_operable",
    "automatic_suspension_armed",
    "no_unresolved_operator_action",
    "supervised_evidence_reviewed",
)


@dataclass(frozen=True)
class GateDecision:
    verdict: GateVerdict
    mode: GateMode
    unmet_precondition_count: int = 0
    permits_automated_action: bool = False
    explanation: str = AUTHORITY_EXPLANATION

    def __post_init__(self) -> None:
        if self.permits_automated_action:
            raise ExecutionGateError("this gate never authorizes automated action")
        if self.verdict is GateVerdict.OPEN and self.mode not in ACTIVATION_APPROVALS:
            # Structural: an open verdict cannot exist while the mode is
            # unapproved, whatever the caller believes about preconditions.
            raise ExecutionGateError("gate mode is not approved for activation")

    @property
    def is_open(self) -> bool:
        return self.verdict is GateVerdict.OPEN

    def sanitized_facts(self) -> dict[str, str]:
        return {
            "gate_verdict": self.verdict.value,
            "gate_mode": self.mode.value,
            "unmet_precondition_bucket": (
                "none" if self.unmet_precondition_count == 0
                else "one" if self.unmet_precondition_count == 1
                else "many"
            ),
            "permits_automated_action": "false",
        }


def open_gate(
    preconditions: GatePreconditions,
    *,
    mode: GateMode = GateMode.SUPERVISED,
    current_monotonic: float,
) -> GateDecision:
    """Evaluate the gate. Cannot return OPEN while no mode is approved.

    Activation is checked **before** preconditions. A caller that has met
    every precondition should be told the gate is not approved, not that some
    condition is missing — the second answer invites someone to go fix the
    condition and try again, which is the wrong lesson entirely.

    `current_monotonic` is required rather than defaulted. Evidence staleness
    is part of whether a precondition holds, and a default clock would let a
    caller omit the one input that makes "recently verified" mean anything.
    """
    if not isinstance(mode, GateMode):
        raise ExecutionGateError("gate mode is required")
    if not isinstance(current_monotonic, (int, float)) or isinstance(
        current_monotonic, bool
    ):
        raise ExecutionGateError("current time must be a number")
    if not isinstance(preconditions, GatePreconditions):
        return GateDecision(GateVerdict.CLOSED_MALFORMED_INPUT, mode)

    unmet = preconditions.unmet(current_monotonic=current_monotonic)
    if mode not in ACTIVATION_APPROVALS:
        return GateDecision(
            GateVerdict.CLOSED_ACTIVATION_NOT_APPROVED, mode, len(unmet)
        )
    if unmet:
        return GateDecision(GateVerdict.CLOSED_PRECONDITION_UNMET, mode, len(unmet))
    return GateDecision(GateVerdict.OPEN, mode)


def open_unattended_gate(
    preconditions: GatePreconditions,
    unattended: UnattendedPreconditions,
    *,
    current_monotonic: float,
) -> GateDecision:
    """The unattended gate. Requires everything supervised does, and more.

    Both records are evaluated against the same evidence rules, and the unmet
    count spans both — an operator reading "one unmet" should not have to ask
    which of two lists it came from.

    Nobody is watching in this mode, which is the reason the evidence bar is
    higher rather than lower: alerting, remote kill, and automatic suspension
    must each be `TESTED`, because a component asserting its own readiness
    proves nothing about machinery that has to fire unattended.
    """
    if not isinstance(current_monotonic, (int, float)) or isinstance(
        current_monotonic, bool
    ):
        raise ExecutionGateError("current time must be a number")
    if not isinstance(unattended, UnattendedPreconditions):
        return GateDecision(GateVerdict.CLOSED_MALFORMED_INPUT, GateMode.LIMITED_UNATTENDED)
    if not isinstance(preconditions, GatePreconditions):
        return GateDecision(GateVerdict.CLOSED_MALFORMED_INPUT, GateMode.LIMITED_UNATTENDED)

    unmet = len(preconditions.unmet(current_monotonic=current_monotonic)) + len(
        unattended.unmet(current_monotonic=current_monotonic)
    )
    if GateMode.LIMITED_UNATTENDED not in ACTIVATION_APPROVALS:
        return GateDecision(
            GateVerdict.CLOSED_ACTIVATION_NOT_APPROVED, GateMode.LIMITED_UNATTENDED, unmet
        )
    if unmet:
        return GateDecision(
            GateVerdict.CLOSED_PRECONDITION_UNMET, GateMode.LIMITED_UNATTENDED, unmet
        )
    return GateDecision(GateVerdict.OPEN, GateMode.LIMITED_UNATTENDED)


def gate_readiness_report() -> Mapping[str, object]:
    """What still stands between here and an openable gate."""
    return {
        "approved_modes": sorted(mode.value for mode in ACTIVATION_APPROVALS),
        "activation_approved": bool(ACTIVATION_APPROVALS),
        "required_precondition_count": len(REQUIRED_PRECONDITIONS),
        "unattended_precondition_count": len(UNATTENDED_PRECONDITIONS),
        "total_unattended_requirement_count": (
            len(REQUIRED_PRECONDITIONS) + len(UNATTENDED_PRECONDITIONS)
        ),
        "self_declared_evidence_accepted_anywhere": any(
            EvidenceSource.SELF_DECLARED in sources
            for sources in REQUIRED_EVIDENCE.values()
        ),
        "gate_is_openable": False,
    }


__all__ = [
    "ACTIVATION_APPROVALS",
    "AUTHORITY_EXPLANATION",
    "MAXIMUM_ATTESTATION_AGE_SECONDS",
    "REQUIRED_EVIDENCE",
    "REQUIRED_PRECONDITIONS",
    "UNATTENDED_PRECONDITIONS",
    "Attestation",
    "EvidenceSource",
    "ExecutionGateError",
    "GateDecision",
    "GateMode",
    "GatePreconditions",
    "GateVerdict",
    "UnattendedPreconditions",
    "evidence_is_acceptable",
    "gate_readiness_report",
    "open_gate",
    "open_unattended_gate",
]
