"""Hard risk controls — limits, duplicate prevention, staleness, kill switch.

Pure and offline. This module decides whether a proposed action **stays inside
operator-set limits**. It does not decide whether the action may happen: that
is a gate's job, and no gate exists yet. `permits_automated_action` is `False`
on every result here.

## The rule that shapes the whole module

**The agent may never raise its own authorized limits.** So `RiskLimits` is
frozen, validated once at construction, and has no method that widens
anything — no `increase`, no `with_higher`, no mutable field. A test asserts
this structurally rather than trusting the convention. Any change to a limit
must come from an operator supplying a different object, reviewed like code.

There are deliberately **no default limit values**. A default is a policy
decision wearing convenience clothes, and a wrong default that nobody chose is
worse than a missing one that fails loudly. Every limit must be supplied.

## Denial is the default

`evaluate` returns `WITHIN_LIMITS` only when every check passes. Anything
unknown, stale, missing, or unrepresentable denies. The kill switch short
circuits everything before any other reasoning happens — a suspended system
should not be computing position sizes.

## Money is Decimal, never float

Limits and values are `Decimal`. A float comparison against a dollar ceiling
is wrong by an amount nobody can predict, and this is the one place in the
codebase where being off by a cent changes an answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Mapping


class RiskLimitError(RuntimeError):
    """A malformed limit or proposal. Never carries broker text."""


class KillSwitchState(str, Enum):
    """Engaged means stop. There is no third state on purpose: a kill switch
    that can be "partially engaged" is not a kill switch."""

    DISENGAGED = "DISENGAGED"
    ENGAGED = "ENGAGED"


class RiskDecision(str, Enum):
    """The only conclusions. Exactly one is not a denial."""

    WITHIN_LIMITS = "WITHIN_LIMITS"

    DENIED_KILL_SWITCH_ENGAGED = "DENIED_KILL_SWITCH_ENGAGED"
    DENIED_POSITION_SIZE = "DENIED_POSITION_SIZE"
    DENIED_ORDER_VALUE = "DENIED_ORDER_VALUE"
    DENIED_DAILY_LOSS = "DENIED_DAILY_LOSS"
    DENIED_OPEN_POSITION_COUNT = "DENIED_OPEN_POSITION_COUNT"
    DENIED_DUPLICATE_ACTION = "DENIED_DUPLICATE_ACTION"
    DENIED_STALE_DATA = "DENIED_STALE_DATA"
    DENIED_MALFORMED_PROPOSAL = "DENIED_MALFORMED_PROPOSAL"


#: Everything that is not `WITHIN_LIMITS`. Derived, so a new denial reason
#: cannot be accidentally omitted from the denial set.
DENIAL_DECISIONS: frozenset[RiskDecision] = frozenset(
    decision for decision in RiskDecision if decision is not RiskDecision.WITHIN_LIMITS
)

AUTHORITY_EXPLANATION = (
    "Staying inside operator-set limits is not permission to act. Execution "
    "authority belongs to a supervised-execution gate that does not exist "
    "yet; this module only establishes that a proposal does not breach a "
    "limit."
)


def _decimal(value: Any, label: str) -> Decimal:
    if isinstance(value, Decimal):
        parsed = value
    elif isinstance(value, int) and not isinstance(value, bool):
        parsed = Decimal(value)
    elif isinstance(value, str):
        try:
            parsed = Decimal(value)
        except InvalidOperation as exc:
            raise RiskLimitError(f"{label} is not a decimal") from exc
    else:
        # Floats are refused rather than converted: an implicit float->Decimal
        # conversion hides the imprecision instead of removing it.
        raise RiskLimitError(f"{label} must be a Decimal, int, or decimal string")
    if not parsed.is_finite():
        raise RiskLimitError(f"{label} is not finite")
    return parsed


@dataclass(frozen=True)
class RiskLimits:
    """Operator-set ceilings. Frozen, validated, and never self-widening.

    Every field is required. There is no default, because a default limit is a
    policy nobody approved.
    """

    maximum_position_quantity: int
    maximum_order_value: Decimal
    maximum_daily_loss: Decimal
    maximum_open_positions: int

    def __post_init__(self) -> None:
        for name in ("maximum_position_quantity", "maximum_open_positions"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise RiskLimitError(f"{name} must be an int")
            if value < 1:
                raise RiskLimitError(f"{name} must be at least 1")
        for name in ("maximum_order_value", "maximum_daily_loss"):
            value = _decimal(getattr(self, name), name)
            if value <= 0:
                raise RiskLimitError(f"{name} must be positive")
            object.__setattr__(self, name, value)


@dataclass(frozen=True)
class ActionProposal:
    """A proposed action, described in the terms limits are expressed in."""

    idempotency_key: str
    quantity: int
    order_value: Decimal
    observed_monotonic: float

    def __post_init__(self) -> None:
        if not isinstance(self.idempotency_key, str) or not self.idempotency_key.strip():
            raise RiskLimitError("an idempotency key is required")
        if not isinstance(self.quantity, int) or isinstance(self.quantity, bool):
            raise RiskLimitError("quantity must be an int")
        if self.quantity < 1:
            raise RiskLimitError("quantity must be at least 1")
        object.__setattr__(
            self, "order_value", _decimal(self.order_value, "order_value")
        )
        if self.order_value <= 0:
            raise RiskLimitError("order value must be positive")
        if not isinstance(self.observed_monotonic, (int, float)) or isinstance(
            self.observed_monotonic, bool
        ):
            raise RiskLimitError("observation time must be a number")


@dataclass(frozen=True)
class AccountRiskState:
    """The current picture the proposal is judged against."""

    realized_daily_loss: Decimal
    open_position_count: int
    #: Quantity already held. Without it, `maximum_position_quantity` would
    #: bound a single order rather than the resulting position, and several
    #: individually legal orders could stack past the ceiling.
    open_position_quantity: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.open_position_quantity, int) or isinstance(
            self.open_position_quantity, bool
        ):
            raise RiskLimitError("open position quantity must be an int")
        if self.open_position_quantity < 0:
            raise RiskLimitError("open position quantity must not be negative")
        object.__setattr__(
            self,
            "realized_daily_loss",
            _decimal(self.realized_daily_loss, "realized_daily_loss"),
        )
        if self.realized_daily_loss < 0:
            # Loss is expressed as a positive magnitude. A negative "loss" is
            # a sign convention mistake, and guessing which way it points is
            # how a limit gets silently inverted.
            raise RiskLimitError("realized daily loss must be a positive magnitude")
        if not isinstance(self.open_position_count, int) or isinstance(
            self.open_position_count, bool
        ):
            raise RiskLimitError("open position count must be an int")
        if self.open_position_count < 0:
            raise RiskLimitError("open position count must not be negative")


class ActionLedgerBase:
    """The ledger contract `evaluate` will accept.

    An explicit base rather than duck typing: the `isinstance` check in
    `evaluate` is a real guard, and replacing it with "has the right method
    names" would let any object with a `has_seen` attribute through. A durable
    implementation subclasses this so the guard keeps its teeth.
    """

    def has_seen(self, key: str) -> bool:
        raise NotImplementedError

    def record(self, key: str, approved_quantity: int) -> None:
        raise NotImplementedError

    @property
    def approved_quantity(self) -> int:
        raise NotImplementedError


class ActionLedger(ActionLedgerBase):
    """Remembers idempotency keys, and how much exposure they approved.

    Deliberately has no removal method. "Forget this key" is indistinguishable
    from "let it happen again", and the whole point is that it cannot.

    It accumulates approved quantity because the caller's reported position is
    not a sufficient bound on its own. A caller that submits several
    individually legal orders without updating what it holds would otherwise
    walk past the ceiling one legal step at a time — three four-unit orders
    against a ten-unit limit, each fine, twelve units held. Tracking what this
    module has already approved closes that without asking the caller to
    remember anything.

    `record` requires the approved quantity rather than defaulting it. A
    default of zero would silently restore the gap for any caller that forgot
    the argument, which is precisely the caller this exists to protect against.
    """

    def __init__(self) -> None:
        self._seen: set[str] = set()
        self._approved_quantity: int = 0

    def has_seen(self, key: str) -> bool:
        if not isinstance(key, str) or not key.strip():
            raise RiskLimitError("an idempotency key is required")
        return key in self._seen

    def record(self, key: str, approved_quantity: int) -> None:
        if self.has_seen(key):
            raise RiskLimitError("action key has already been used")
        if not isinstance(approved_quantity, int) or isinstance(
            approved_quantity, bool
        ):
            raise RiskLimitError("approved quantity must be an int")
        if approved_quantity < 0:
            raise RiskLimitError("approved quantity must not be negative")
        self._seen.add(key)
        self._approved_quantity += approved_quantity

    @property
    def approved_quantity(self) -> int:
        """Total quantity this ledger has approved. Never decreases."""
        return self._approved_quantity

    @property
    def recorded_count(self) -> int:
        return len(self._seen)


@dataclass(frozen=True)
class RiskAssessment:
    decision: RiskDecision
    permits_automated_action: bool = False
    explanation: str = AUTHORITY_EXPLANATION

    def __post_init__(self) -> None:
        if self.permits_automated_action:
            raise RiskLimitError("risk assessment never authorizes action")

    @property
    def is_denial(self) -> bool:
        return self.decision in DENIAL_DECISIONS

    def sanitized_facts(self) -> dict[str, str]:
        """Fixed keys, fixed values. No amount, key, quantity, or symbol."""
        return {
            "risk_decision": self.decision.value,
            "is_denial": "true" if self.is_denial else "false",
            "permits_automated_action": "false",
        }


def evaluate(
    proposal: ActionProposal,
    limits: RiskLimits,
    state: AccountRiskState,
    *,
    kill_switch: KillSwitchState,
    ledger: ActionLedger,
    current_monotonic: float,
    maximum_data_age_seconds: float = 5.0,
) -> RiskAssessment:
    """Deny unless every check passes. Order is deliberate.

    The kill switch is evaluated first and alone: a suspended system should
    not be computing position sizes, and a denial that reports a size breach
    while the switch is engaged tells an operator the wrong thing about why
    the system stopped.

    Duplicate detection comes before the limit arithmetic for the same reason
    — a replayed action is a different problem from an oversized one, and
    reporting the second hides the first.
    """
    if not isinstance(kill_switch, KillSwitchState):
        raise RiskLimitError("kill switch state is required")
    if kill_switch is KillSwitchState.ENGAGED:
        return RiskAssessment(RiskDecision.DENIED_KILL_SWITCH_ENGAGED)

    if not isinstance(proposal, ActionProposal):
        return RiskAssessment(RiskDecision.DENIED_MALFORMED_PROPOSAL)
    if not isinstance(limits, RiskLimits) or not isinstance(state, AccountRiskState):
        raise RiskLimitError("limits and account state are required")
    if not isinstance(ledger, ActionLedgerBase):
        raise RiskLimitError("an action ledger is required")
    if not isinstance(current_monotonic, (int, float)) or isinstance(
        current_monotonic, bool
    ):
        raise RiskLimitError("current time must be a number")

    if ledger.has_seen(proposal.idempotency_key):
        return RiskAssessment(RiskDecision.DENIED_DUPLICATE_ACTION)

    age = current_monotonic - proposal.observed_monotonic
    if age < 0 or age > maximum_data_age_seconds:
        # Data from the future is as untrustworthy as data that is too old.
        return RiskAssessment(RiskDecision.DENIED_STALE_DATA)

    # Judged against the *resulting* position, not the order alone, and
    # against the larger of two independent views of current exposure:
    #
    #   * what the caller says it holds, and
    #   * what this module has already approved.
    #
    # The max rather than the sum, because the two usually describe the same
    # orders and adding them would double-count. Taking the larger keeps the
    # bound correct whether or not the caller updates its own state, and
    # whether or not approved orders have filled yet.
    exposure = max(state.open_position_quantity, ledger.approved_quantity)
    if exposure + proposal.quantity > limits.maximum_position_quantity:
        return RiskAssessment(RiskDecision.DENIED_POSITION_SIZE)
    if proposal.order_value > limits.maximum_order_value:
        return RiskAssessment(RiskDecision.DENIED_ORDER_VALUE)
    if state.realized_daily_loss >= limits.maximum_daily_loss:
        # At the limit is already too far: the next loss would exceed it.
        return RiskAssessment(RiskDecision.DENIED_DAILY_LOSS)
    if state.open_position_count >= limits.maximum_open_positions:
        return RiskAssessment(RiskDecision.DENIED_OPEN_POSITION_COUNT)

    return RiskAssessment(RiskDecision.WITHIN_LIMITS)


__all__ = [
    "AUTHORITY_EXPLANATION",
    "DENIAL_DECISIONS",
    "AccountRiskState",
    "ActionLedger",
    "ActionLedgerBase",
    "ActionProposal",
    "KillSwitchState",
    "RiskAssessment",
    "RiskDecision",
    "RiskLimitError",
    "RiskLimits",
    "evaluate",
]
