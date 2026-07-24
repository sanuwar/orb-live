"""
Live equity-order contract boundary (OFFICIAL OUTPUT SCHEMAS UNAVAILABLE).

This module holds ONLY what is grounded:

  * the equity-order fields actually observed in a live order-status read
    during Checkpoint 7B;
  * a strict, decimal-safe remaining-quantity derivation, required because
    NO remaining-quantity field is returned by the broker;
  * protocol-neutral result types and a fail-closed boundary for the
    place / cancel response contracts, which remain UNESTABLISHED.

It performs NO broker, MCP, network, credential, token, or database access
and invokes no tool.

RESPONSE-CONTRACT BOUNDARY
--------------------------
The official outputSchema dump is not available locally, and the place and
cancel tools are prohibited outside a separately authorized real-order
checkpoint. Therefore this module deliberately does NOT parse a
place_equity_order or cancel_equity_order response. It asserts nothing about
``data.order``, ``data.accepted``, where an order id appears, or any
cancellation-completion semantics beyond the grounded fact that cancellation
is asynchronous. Any attempt to interpret those responses fails closed with
``ResponseContractUnavailableError``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any

try:
    from .broker_action_saga import normalized_decimal_text
except ImportError:  # pragma: no cover - direct-module import fallback
    from broker_action_saga import normalized_decimal_text


class LiveOrderContractError(RuntimeError):
    """Base class for live order-contract violations."""


class QuantityDerivationError(LiveOrderContractError):
    """A quantity value was malformed or an impossible fill state."""


class ResponseContractUnavailableError(LiveOrderContractError):
    """
    A response contract that has not been established was required.

    The official outputSchema dump is unavailable locally and no live
    place/cancel response has ever been observed. Failing closed is the only
    honest behaviour; inventing fields is prohibited.
    """


# --------------------------------------------------------------------------
# Observed order-status fields (Checkpoint 7B).
# --------------------------------------------------------------------------

class ObservedOrderField(str, Enum):
    """
    Equity-order fields OBSERVED in a live get_equity_orders response.

    Only field NAMES are recorded here. No historical scalar value from any
    real order is reproduced anywhere in this repository.
    """

    ID = "id"
    STATE = "state"
    QUANTITY = "quantity"
    CUMULATIVE_QUANTITY = "cumulative_quantity"
    AVERAGE_PRICE = "average_price"
    SIDE = "side"
    SYMBOL = "symbol"
    TYPE = "type"
    PRICE = "price"
    STOP_PRICE = "stop_price"
    CREATED_AT = "created_at"
    LAST_TRANSACTION_AT = "last_transaction_at"


OBSERVED_ORDER_FIELDS: frozenset[str] = frozenset(
    field.value for field in ObservedOrderField
)

# Deliberately NOT present in any observed order record.
ABSENT_ORDER_FIELDS: frozenset[str] = frozenset(
    {"ref_id", "remaining_quantity"}
)


# --------------------------------------------------------------------------
# Decimal-safe remaining-quantity derivation.
# --------------------------------------------------------------------------

def _strict_decimal(value: Any, label: str) -> Decimal:
    """
    Convert to Decimal without binary floating point, or fail closed.

    ``float`` is rejected outright: accepting it would silently introduce
    binary floating-point error into a quantity.
    """
    if isinstance(value, bool) or value is None:
        raise QuantityDerivationError(
            f"The {label} is missing or not a number."
        )

    if isinstance(value, float):
        raise QuantityDerivationError(
            f"The {label} must not be a binary float; supply a decimal "
            "string or Decimal."
        )

    if isinstance(value, Decimal):
        candidate = value
    elif isinstance(value, int):
        candidate = Decimal(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise QuantityDerivationError(
                f"The {label} is empty."
            )
        try:
            candidate = Decimal(text)
        except InvalidOperation:
            raise QuantityDerivationError(
                f"The {label} is not a valid decimal number."
            ) from None
    else:
        raise QuantityDerivationError(
            f"The {label} has an unsupported type."
        )

    if not candidate.is_finite():
        raise QuantityDerivationError(
            f"The {label} is not finite."
        )

    return candidate


@dataclass(frozen=True)
class DerivedQuantities:
    """
    A validated fill state.

    ``remaining`` is DERIVED, never read from the broker: Checkpoint 7B
    observed no remaining-quantity field in any order record.
    """

    submitted: Decimal
    cumulative: Decimal
    remaining: Decimal

    @property
    def is_fully_filled(self) -> bool:
        return self.remaining == 0

    @property
    def is_partially_filled(self) -> bool:
        return self.cumulative > 0 and self.remaining > 0

    def as_text(self) -> dict[str, str]:
        """Normalized decimal text using the existing project rules."""
        return {
            "submitted_quantity": normalized_decimal_text(self.submitted),
            "cumulative_quantity": normalized_decimal_text(self.cumulative),
            "remaining_quantity": normalized_decimal_text(self.remaining),
        }


def derive_quantities(
    quantity: Any,
    cumulative_quantity: Any,
) -> DerivedQuantities:
    """
    Derive remaining quantity as ``quantity - cumulative_quantity``.

    Fails closed on a malformed numeric string, a negative submitted or
    cumulative quantity, or a cumulative quantity greater than the submitted
    quantity. Invalid values are NEVER silently clamped.
    """
    submitted = _strict_decimal(quantity, "submitted quantity")
    cumulative = _strict_decimal(
        cumulative_quantity, "cumulative quantity"
    )

    if submitted < 0:
        raise QuantityDerivationError(
            "The submitted quantity must not be negative."
        )

    if cumulative < 0:
        raise QuantityDerivationError(
            "The cumulative quantity must not be negative."
        )

    if cumulative > submitted:
        raise QuantityDerivationError(
            "The cumulative quantity exceeds the submitted quantity; "
            "refusing to clamp an impossible fill state."
        )

    return DerivedQuantities(
        submitted=submitted,
        cumulative=cumulative,
        remaining=submitted - cumulative,
    )


def remaining_quantity(
    quantity: Any,
    cumulative_quantity: Any,
) -> Decimal:
    """Convenience wrapper returning only the derived remaining quantity."""
    return derive_quantities(quantity, cumulative_quantity).remaining


# --------------------------------------------------------------------------
# Protocol-neutral result types and the fail-closed response boundary.
# --------------------------------------------------------------------------

class BrokerCallOutcome(str, Enum):
    """
    Protocol-neutral classification of a broker call.

    Deliberately transport-agnostic: it encodes no assumption about response
    field names, envelopes, or broker state values.
    """

    CONFIRMED_SUCCEEDED = "confirmed_succeeded"
    CONFIRMED_FAILED = "confirmed_failed"
    OUTCOME_UNKNOWN = "outcome_unknown"


@dataclass(frozen=True)
class BrokerCallResult:
    """
    A protocol-neutral broker-call result.

    Carries no raw payload and no broker-specific field. ``detail`` is an
    authored, non-sensitive label only.
    """

    outcome: BrokerCallOutcome
    detail: str

    @property
    def is_ambiguous(self) -> bool:
        return self.outcome is BrokerCallOutcome.OUTCOME_UNKNOWN


# Response contracts that have NOT been established. Any attempt to
# interpret one of these fails closed.
UNESTABLISHED_RESPONSE_CONTRACTS: frozenset[str] = frozenset(
    {"place_equity_order", "cancel_equity_order"}
)


def require_response_contract(tool_name: str) -> None:
    """
    Fail closed when a response contract has not been established.

    This is the single chokepoint that prevents an invented parser from
    being introduced before the official output schemas are supplied.
    """
    normalized = str(tool_name).strip()

    if normalized in UNESTABLISHED_RESPONSE_CONTRACTS:
        raise ResponseContractUnavailableError(
            f"The response contract for {normalized!r} has not been "
            "established. The official output schema is unavailable and no "
            "live response has been observed. Refusing to guess."
        )


def parse_place_response(_response: Any) -> BrokerCallResult:
    """
    Placeholder that ALWAYS fails closed.

    A real parser requires the official place_equity_order output schema,
    which is unavailable. This function exists so the boundary is explicit
    and testable, not so it can be quietly filled in.
    """
    require_response_contract("place_equity_order")
    raise ResponseContractUnavailableError(  # pragma: no cover - defensive
        "Unreachable: the place response contract is unavailable."
    )


def parse_cancel_response(_response: Any) -> BrokerCallResult:
    """
    Placeholder that ALWAYS fails closed.

    Cancellation is known to be ASYNCHRONOUS: acceptance would not mean
    completion, and final state would have to be confirmed by an order-status
    read. No stronger semantics are assumed, and no response field is
    presumed to exist.
    """
    require_response_contract("cancel_equity_order")
    raise ResponseContractUnavailableError(  # pragma: no cover - defensive
        "Unreachable: the cancel response contract is unavailable."
    )
