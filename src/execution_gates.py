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


@dataclass(frozen=True)
class GatePreconditions:
    """Facts the caller has already established elsewhere.

    Every field is required and every one must be `True` to open. There are no
    defaults: a default would mean an unstated precondition counts as
    satisfied, which is the failure mode this whole design exists to avoid.
    """

    #: A human is present and has acknowledged this specific session.
    operator_present_and_acknowledged: bool
    #: `strategy.enabled` is true in reviewed configuration.
    strategy_enabled: bool
    #: At least one protection state has been approved by human review.
    protection_grounding_complete: bool
    #: Broker state was read successfully and is fresh.
    broker_state_verified_fresh: bool
    #: Restart recovery reported no inconsistency.
    recovery_consistent: bool
    #: The proposed action is inside operator-set limits.
    risk_within_limits: bool
    #: The kill switch is disengaged.
    kill_switch_disengaged: bool
    #: Monitoring is running and its heartbeat is healthy.
    monitoring_healthy: bool
    #: The market is in a session where this action is valid.
    market_session_valid: bool

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if not isinstance(value, bool):
                # A truthy non-bool is how "unknown" sneaks in as "yes".
                raise ExecutionGateError(f"{item.name} must be a bool")

    def unmet(self) -> tuple[str, ...]:
        return tuple(
            item.name for item in fields(self) if getattr(self, item.name) is not True
        )

    @property
    def all_met(self) -> bool:
        return not self.unmet()


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
    capital_tier_approved: bool
    #: The deployment's expiry has not passed.
    deployment_not_expired: bool
    #: Trading is inside the approved window for this deployment.
    within_permitted_trading_window: bool
    #: Alert delivery was verified reachable *before* the session began.
    alerting_verified_reachable: bool
    #: The kill switch can be operated without physical access to the host.
    kill_switch_remotely_operable: bool
    #: Automatic suspension is armed and was tested.
    automatic_suspension_armed: bool
    #: No prior run ended requiring operator attention.
    no_unresolved_operator_action: bool
    #: Supervised runs were reviewed and judged sufficient evidence.
    supervised_evidence_reviewed: bool

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if not isinstance(value, bool):
                raise ExecutionGateError(f"{item.name} must be a bool")

    def unmet(self) -> tuple[str, ...]:
        return tuple(
            item.name for item in fields(self) if getattr(self, item.name) is not True
        )

    @property
    def all_met(self) -> bool:
        return not self.unmet()


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
    preconditions: GatePreconditions, *, mode: GateMode = GateMode.SUPERVISED
) -> GateDecision:
    """Evaluate the gate. Cannot return OPEN while no mode is approved.

    Activation is checked **before** preconditions. A caller that has met
    every precondition should be told the gate is not approved, not that some
    condition is missing — the second answer invites someone to go fix the
    condition and try again, which is the wrong lesson entirely.
    """
    if not isinstance(mode, GateMode):
        raise ExecutionGateError("gate mode is required")
    if not isinstance(preconditions, GatePreconditions):
        return GateDecision(GateVerdict.CLOSED_MALFORMED_INPUT, mode)

    if mode not in ACTIVATION_APPROVALS:
        return GateDecision(
            GateVerdict.CLOSED_ACTIVATION_NOT_APPROVED,
            mode,
            len(preconditions.unmet()),
        )

    unmet = preconditions.unmet()
    if unmet:
        return GateDecision(GateVerdict.CLOSED_PRECONDITION_UNMET, mode, len(unmet))
    return GateDecision(GateVerdict.OPEN, mode)


def open_unattended_gate(
    preconditions: GatePreconditions, unattended: UnattendedPreconditions
) -> GateDecision:
    """The unattended gate. Requires everything supervised does, and more.

    Both records are evaluated, and the unmet count spans both — an operator
    reading "one unmet" should not have to ask which of two lists it came
    from.
    """
    if not isinstance(unattended, UnattendedPreconditions):
        return GateDecision(GateVerdict.CLOSED_MALFORMED_INPUT, GateMode.LIMITED_UNATTENDED)
    if not isinstance(preconditions, GatePreconditions):
        return GateDecision(GateVerdict.CLOSED_MALFORMED_INPUT, GateMode.LIMITED_UNATTENDED)

    unmet = len(preconditions.unmet()) + len(unattended.unmet())
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
        "gate_is_openable": False,
    }


__all__ = [
    "ACTIVATION_APPROVALS",
    "AUTHORITY_EXPLANATION",
    "REQUIRED_PRECONDITIONS",
    "UNATTENDED_PRECONDITIONS",
    "ExecutionGateError",
    "GateDecision",
    "GateMode",
    "GatePreconditions",
    "GateVerdict",
    "UnattendedPreconditions",
    "gate_readiness_report",
    "open_gate",
    "open_unattended_gate",
]
