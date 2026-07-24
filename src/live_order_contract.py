"""
Live equity-order contract boundary (OUTPUT SCHEMAS NOW GROUNDED).

This module holds ONLY what is grounded:

  * the advertised place_equity_order and cancel_equity_order response
    contracts, taken from the official ``tools/list`` schema snapshot
    (see SCHEMA_SNAPSHOT_* below);
  * the equity-order fields actually observed in a live order-status read
    during Checkpoint 7B;
  * a strict, decimal-safe remaining-quantity derivation, required because
    NO remaining-quantity field is advertised or returned by the broker;
  * protocol-neutral immutable result types for the two write-tool
    responses.

It performs NO broker, MCP, network, credential, token, or database access
and invokes no tool. Parsing a response here is a PURE function over a
mapping the caller already holds; nothing in this module can obtain one.

RESPONSE-CONTRACT GROUNDING
---------------------------
The official schema snapshot is an OPERATOR-SUPPLIED, UNTRACKED external
reference file. It is a ``tools/list`` dump: it advertises INPUT and OUTPUT
schemas. It is NOT a live observation. Everything derived from it is
SCHEMA-ADVERTISED and must never be described as live-verified.

Two facts that this module keeps strictly separate:

  SUBMISSION IS NOT FILL       a place response proves an order was
                               created, never that it filled. See
                               ``ParsedPlaceResponse.establishes_submission_only``.
  ACCEPTANCE IS NOT COMPLETION a cancel response proves the broker accepted
                               a cancel REQUEST. Final cancellation state is
                               only ever established by a subsequent
                               order-status read.

Neither parser infers cancellation, protective coverage, or terminal status
from a successful parse.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Mapping

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

    Retained as the fail-closed chokepoint for any tool whose contract is
    still unknown. The place and cancel contracts are no longer on that
    list; see ``UNESTABLISHED_RESPONSE_CONTRACTS``.
    """


class ResponseContractViolationError(LiveOrderContractError):
    """
    A response did not match the advertised output schema.

    The message names the offending FIELD only. Values, guide text,
    reject reasons, and raw payloads are never included: an exception
    message is an ordinary log surface.
    """


# --------------------------------------------------------------------------
# Provenance of the advertised contracts.
# --------------------------------------------------------------------------

# The snapshot is an external, untracked, operator-supplied reference file.
# Its path is deliberately NOT recorded here: this module must remain
# independent of any operator-local filesystem layout.
SCHEMA_SNAPSHOT_DATE = "2026-07-17"
SCHEMA_SNAPSHOT_KIND = "tools/list schema snapshot (input and output schemas)"
SCHEMA_SNAPSHOT_SHA256 = (
    "4cbc0359c7fd734799c2a7f11cd96b5c024c6de4145cbbf9a56100eefd984eed"
)


# --------------------------------------------------------------------------
# Observed order-status fields (Checkpoint 7B) vs. advertised fields.
# --------------------------------------------------------------------------

class ObservedOrderField(str, Enum):
    """
    Equity-order fields OBSERVED in a live get_equity_orders response.

    Only field NAMES are recorded here. No historical scalar value from any
    real order is reproduced anywhere in this repository.

    This is the set of CANDIDATE fields Checkpoint 7B probed for and
    matched. It is deliberately NOT a claim that the live response contained
    no other field: 7B recorded presence booleans for a fixed candidate
    list, not an exhaustive union. The advertised set is therefore larger,
    and that is not a contradiction.
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

# Deliberately NOT present in any observed order record, and — independently
# — NOT advertised in either equity-order output schema. Two separate lines
# of evidence agreeing, not one restated.
ABSENT_ORDER_FIELDS: frozenset[str] = frozenset(
    {"ref_id", "remaining_quantity"}
)

# Required by the advertised order object (place_equity_order.data.order and
# get_equity_orders.data.orders[] declare the SAME shape).
ORDER_REQUIRED_FIELDS: frozenset[str] = frozenset(
    {
        "id",
        "instrument_id",
        "symbol",
        "side",
        "type",
        "state",
        "quantity",
        "cumulative_quantity",
        "price",
        "stop_price",
        "average_price",
        "fees",
        "dollar_based_amount",
        "time_in_force",
        "market_hours",
        "trigger",
        "placed_agent",
        "created_at",
        "last_transaction_at",
        "executions",
    }
)

# The only advertised optional field. The object declares
# additionalProperties=false, so nothing else may appear.
ORDER_OPTIONAL_FIELDS: frozenset[str] = frozenset({"reject_reason"})

ORDER_ALLOWED_FIELDS: frozenset[str] = (
    ORDER_REQUIRED_FIELDS | ORDER_OPTIONAL_FIELDS
)

_EXECUTION_REQUIRED_FIELDS: frozenset[str] = frozenset(
    {"id", "price", "quantity", "timestamp", "fees"}
)

_DOLLAR_AMOUNT_REQUIRED_FIELDS: frozenset[str] = frozenset(
    {"amount", "currency_code"}
)

# Fields whose advertised type is a bare "string" (never null).
_ORDER_REQUIRED_TEXT_FIELDS: frozenset[str] = frozenset(
    {
        "id",
        "instrument_id",
        "symbol",
        "side",
        "type",
        "state",
        "time_in_force",
        "market_hours",
        "trigger",
        "placed_agent",
        "created_at",
    }
)

# Fields whose advertised type is ["null", "string"].
_ORDER_NULLABLE_TEXT_FIELDS: frozenset[str] = frozenset(
    {"quantity", "price", "stop_price", "average_price", "last_transaction_at"}
)


# --------------------------------------------------------------------------
# Advertised lifecycle states.
# --------------------------------------------------------------------------

# The OUTPUT `state` description of BOTH equity-order output schemas. This
# is the authoritative lifecycle set for a RETURNED order.
ADVERTISED_ORDER_OUTPUT_STATES: frozenset[str] = frozenset(
    {
        "new",
        "queued",
        "unconfirmed",
        "partially_filled",
        "filled",
        "cancelled",
        "rejected",
        "failed",
        "voided",
        "pending_cancelled",
        "partially_filled_rest_cancelled",
        # Short-sale-specific locating states.
        "locating",
        "locate_failed",
    }
)

# The INPUT `state` FILTER description of get_equity_orders. It is a
# different, narrower list and is NOT the output contract. Recorded so the
# two are never conflated again.
ADVERTISED_ORDER_INPUT_FILTER_STATES: frozenset[str] = frozenset(
    {
        "new",
        "queued",
        "confirmed",
        "unconfirmed",
        "partially_filled",
        "filled",
        "cancelled",
        "rejected",
        "failed",
        "voided",
    }
)

# States advertised for OUTPUT but absent from the INPUT filter list. The
# earlier record wrongly concluded from the input filter alone that
# pending_cancelled and partially_filled_rest_cancelled were not equity
# lifecycle states. They are.
OUTPUT_ONLY_ORDER_STATES: frozenset[str] = (
    ADVERTISED_ORDER_OUTPUT_STATES - ADVERTISED_ORDER_INPUT_FILTER_STATES
)

# Present in the INPUT filter but absent from the OUTPUT state description.
# The asymmetry runs both ways; neither list is silently preferred.
INPUT_FILTER_ONLY_ORDER_STATES: frozenset[str] = (
    ADVERTISED_ORDER_INPUT_FILTER_STATES - ADVERTISED_ORDER_OUTPUT_STATES
)

# States actually seen live during Checkpoint 7B across the sampled records.
CHECKPOINT_7B_OBSERVED_STATES: frozenset[str] = frozenset(
    {"filled", "cancelled"}
)


# --------------------------------------------------------------------------
# ref_id conclusions.
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class RefIdFindings:
    """
    What is established about ref_id, from both evidence lines.

    ref_id is a SUBMISSION-side idempotency key only. It is not returned,
    not filterable, and cannot be used to rediscover an order.
    """

    place_input_accepts_ref_id: bool
    place_output_advertises_ref_id: bool
    order_status_output_advertises_ref_id: bool
    checkpoint_7b_records_sampled: int
    checkpoint_7b_ref_id_observed: bool
    lookup_by_ref_supported: bool
    same_ref_is_local_payload_integrity_only: bool
    automatic_retry_authorized: bool


REF_ID_FINDINGS = RefIdFindings(
    place_input_accepts_ref_id=True,
    place_output_advertises_ref_id=False,
    order_status_output_advertises_ref_id=False,
    checkpoint_7b_records_sampled=13,
    checkpoint_7b_ref_id_observed=False,
    lookup_by_ref_supported=False,
    same_ref_is_local_payload_integrity_only=True,
    automatic_retry_authorized=False,
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

    ``remaining`` is DERIVED, never read from the broker: neither equity
    output schema advertises a remaining-quantity field, and Checkpoint 7B
    observed none.
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
# Strict schema validation helpers.
# --------------------------------------------------------------------------

def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    """A schema ``object`` must be an actual mapping."""
    if not isinstance(value, Mapping):
        raise ResponseContractViolationError(
            f"The {label} must be an object."
        )
    return value


def _require_exact_keys(
    payload: Mapping[str, Any],
    *,
    required: frozenset[str],
    optional: frozenset[str],
    label: str,
) -> None:
    """
    Enforce ``required`` plus ``additionalProperties: false``.

    Reports the offending FIELD NAMES only; a field name is part of the
    published schema, never a value.
    """
    keys = {str(key) for key in payload}

    missing = required - keys
    if missing:
        raise ResponseContractViolationError(
            f"The {label} is missing required fields: "
            + ", ".join(sorted(missing))
            + "."
        )

    unexpected = keys - required - optional
    if unexpected:
        raise ResponseContractViolationError(
            f"The {label} declares additionalProperties false but "
            "contained unexpected fields: "
            + ", ".join(sorted(unexpected))
            + "."
        )


def _require_text(value: Any, label: str) -> str:
    """A required schema ``string``: present, a real str, and non-blank."""
    if isinstance(value, bool) or value is None:
        raise ResponseContractViolationError(
            f"The {label} is missing or null but is required."
        )

    if not isinstance(value, str):
        raise ResponseContractViolationError(
            f"The {label} must be a string."
        )

    if not value.strip():
        raise ResponseContractViolationError(
            f"The {label} must not be blank."
        )

    return value


def _optional_text(value: Any, label: str) -> str | None:
    """A schema ``["null", "string"]`` field."""
    if value is None:
        return None

    return _require_text(value, label)


def _require_decimal_text(value: Any, label: str) -> Decimal:
    """
    A required numeric field advertised as a ``string``.

    The wire type is a string, so a Python float, int, or Decimal is a
    schema violation rather than a convenience. Floats are called out
    explicitly because silently accepting one would inject binary
    floating-point error into money or share counts.
    """
    if isinstance(value, float):
        raise ResponseContractViolationError(
            f"The {label} must not be a binary float; the schema "
            "advertises a decimal string."
        )

    text = _require_text(value, label)

    try:
        return _strict_decimal(text, label)
    except QuantityDerivationError as error:
        raise ResponseContractViolationError(str(error)) from None


def _optional_decimal_text(value: Any, label: str) -> Decimal | None:
    """A nullable numeric field advertised as ``["null", "string"]``."""
    if value is None:
        return None

    return _require_decimal_text(value, label)


def _require_positive(amount: Decimal | None, label: str) -> Decimal | None:
    """A price must be strictly positive when present."""
    if amount is not None and amount <= 0:
        raise ResponseContractViolationError(
            f"The {label} must be greater than zero."
        )
    return amount


def _parse_timestamp(text: str | None) -> datetime | None:
    """
    Best-effort ISO-8601 parse.

    The schema advertises no timestamp format, so an unparseable value is
    NOT a schema violation: the raw text is preserved and the parsed value
    is simply absent. Nothing downstream may assume a parsed timestamp.
    """
    if text is None:
        return None

    candidate = text.strip()
    if candidate.endswith(("Z", "z")):
        candidate = candidate[:-1] + "+00:00"

    try:
        return datetime.fromisoformat(candidate)
    except ValueError:
        return None


def _validate_dollar_based_amount(value: Any) -> None:
    """
    Validate the nullable notional object.

    Its contents are not carried into the result: a share-quantity
    commissioning order never uses them, and copying them would widen the
    result beyond the operational minimum.
    """
    if value is None:
        return

    payload = _require_mapping(value, "order dollar_based_amount")
    _require_exact_keys(
        payload,
        required=_DOLLAR_AMOUNT_REQUIRED_FIELDS,
        optional=frozenset(),
        label="order dollar_based_amount",
    )
    _require_decimal_text(
        payload.get("amount"), "order dollar_based_amount amount"
    )
    _require_text(
        payload.get("currency_code"),
        "order dollar_based_amount currency_code",
    )


def _validate_executions(value: Any) -> int:
    """
    Validate the nullable per-fill array and return its length.

    Only the COUNT is carried forward. Per-execution prices and identifiers
    are validated and discarded: they are not needed by an adapter and are
    exactly the kind of detail that should not spread.
    """
    if value is None:
        return 0

    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ResponseContractViolationError(
            "The order executions field must be an array or null."
        )

    for index, item in enumerate(value):
        label = f"order execution at index {index}"
        execution = _require_mapping(item, label)
        _require_exact_keys(
            execution,
            required=_EXECUTION_REQUIRED_FIELDS,
            optional=frozenset(),
            label=label,
        )
        _require_text(execution.get("id"), f"{label} id")
        _require_text(execution.get("timestamp"), f"{label} timestamp")
        _require_positive(
            _require_decimal_text(execution.get("price"), f"{label} price"),
            f"{label} price",
        )
        _require_decimal_text(execution.get("quantity"), f"{label} quantity")
        _require_decimal_text(execution.get("fees"), f"{label} fees")

    return len(value)


# --------------------------------------------------------------------------
# Protocol-neutral result types.
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


@dataclass(frozen=True)
class ParsedPlaceResponse:
    """
    The operational content of an advertised place_equity_order response.

    Immutable and protocol-neutral: it carries the fields a future adapter
    needs and nothing else. Guide text, reject reasons, per-execution
    detail, notional amounts, and the raw payload are validated where the
    schema demands it and then deliberately dropped.

    ``state`` is the broker state EXACTLY as returned. It is not
    interpreted here; classification is a separate, conservative step.
    """

    server_order_id: str
    state: str
    symbol: str
    side: str
    order_type: str
    trigger: str
    submitted_quantity: Decimal | None
    cumulative_quantity: Decimal
    remaining_quantity: Decimal | None
    limit_price: Decimal | None
    stop_price: Decimal | None
    average_fill_price: Decimal | None
    time_in_force: str
    market_hours: str
    created_at: str
    created_at_utc: datetime | None
    last_transaction_at: str | None
    last_transaction_at_utc: datetime | None
    execution_count: int
    establishes_submission_only: bool

    @property
    def has_any_reported_fill(self) -> bool:
        """
        Whether the SUBMISSION response reported a non-zero cumulative fill.

        This is a report of what the response said, not a verified fill. A
        verified fill requires an order-status read.
        """
        return self.cumulative_quantity > 0


class CancelAcceptance(str, Enum):
    """
    The only two conclusions a cancel response supports.

    Neither is ever named CANCELLED. ``accepted`` describes the REQUEST, not
    the order.
    """

    CANCEL_REQUEST_ACCEPTED_ASYNC = "CANCEL_REQUEST_ACCEPTED_ASYNC"
    CANCEL_REQUEST_NOT_ACCEPTED = "CANCEL_REQUEST_NOT_ACCEPTED"


@dataclass(frozen=True)
class ParsedCancelResponse:
    """
    The operational content of an advertised cancel_equity_order response.

    The advertised payload is a single boolean. It establishes whether the
    broker ACCEPTED a cancel request. It never establishes that the order
    was cancelled, and this type exposes no attribute that could be read
    that way.
    """

    acceptance: CancelAcceptance
    order_status_reconciliation_required: bool

    @property
    def request_was_accepted(self) -> bool:
        """
        Whether the broker accepted the cancel REQUEST.

        True here means the request is in flight. It does NOT mean the order
        is cancelled and must never be used as a cancellation proof.
        """
        return self.acceptance is CancelAcceptance.CANCEL_REQUEST_ACCEPTED_ASYNC


# --------------------------------------------------------------------------
# The fail-closed chokepoint (retained for still-unknown contracts).
# --------------------------------------------------------------------------

# EMPTY: the place and cancel output contracts are now grounded in the
# official schema snapshot. The chokepoint itself is retained so a future
# tool whose contract is unknown still fails closed by name.
UNESTABLISHED_RESPONSE_CONTRACTS: frozenset[str] = frozenset()


def require_response_contract(tool_name: str) -> None:
    """
    Fail closed when a response contract has not been established.

    This remains the single chokepoint preventing an invented parser from
    being introduced for a tool whose output schema is unknown.
    """
    normalized = str(tool_name).strip()

    if normalized in UNESTABLISHED_RESPONSE_CONTRACTS:
        raise ResponseContractUnavailableError(
            f"The response contract for {normalized!r} has not been "
            "established. No official output schema is available and no "
            "live response has been observed. Refusing to guess."
        )


# --------------------------------------------------------------------------
# Strict place-response parsing.
# --------------------------------------------------------------------------

def parse_place_response(response: Any) -> ParsedPlaceResponse:
    """
    Parse an advertised place_equity_order success response.

    Envelope (all levels declare ``additionalProperties: false``)::

        {"data": {"order": {...}}, "guide": "..."}

    ``guide`` is validated as a required string and then DISCARDED. It is
    prose written for a chat assistant, not operational data, and it is
    never copied into a result, a log, or an exception message.

    A successful parse establishes SUBMISSION ONLY. It does not establish a
    fill, a cancellation, protective coverage, or any terminal status, even
    when the returned state reads as terminal. Verification requires a
    subsequent order-status read.

    ``ref_id`` is NOT expected: the advertised order object does not declare
    it, and additionalProperties=false therefore makes a returned ref_id a
    schema violation.
    """
    envelope = _require_mapping(response, "place response")
    _require_exact_keys(
        envelope,
        required=frozenset({"data", "guide"}),
        optional=frozenset(),
        label="place response",
    )
    # Validated for shape, then dropped. Never parsed as operational data.
    _require_text(envelope.get("guide"), "place response guide")

    data = _require_mapping(envelope.get("data"), "place response data")
    _require_exact_keys(
        data,
        required=frozenset({"order"}),
        optional=frozenset(),
        label="place response data",
    )

    raw_order = data.get("order")
    if raw_order is None:
        raise ResponseContractViolationError(
            "The place response data.order is null; a submission response "
            "without an order object establishes nothing."
        )

    order = _require_mapping(raw_order, "place response order")
    _require_exact_keys(
        order,
        required=ORDER_REQUIRED_FIELDS,
        optional=ORDER_OPTIONAL_FIELDS,
        label="place response order",
    )

    for name in sorted(_ORDER_REQUIRED_TEXT_FIELDS):
        _require_text(order.get(name), f"place response order {name}")

    # reject_reason is optional; validated for type only and then dropped.
    # It is free-text written by the broker and must not reach a log.
    if "reject_reason" in order:
        _require_text(
            order.get("reject_reason"), "place response order reject_reason"
        )

    _validate_dollar_based_amount(order.get("dollar_based_amount"))
    execution_count = _validate_executions(order.get("executions"))

    cumulative = _require_decimal_text(
        order.get("cumulative_quantity"),
        "place response order cumulative_quantity",
    )
    if cumulative < 0:
        raise ResponseContractViolationError(
            "The place response order cumulative_quantity must not be "
            "negative."
        )

    _require_decimal_text(order.get("fees"), "place response order fees")

    submitted = _optional_decimal_text(
        order.get("quantity"), "place response order quantity"
    )

    # Advertised as nullable for dollar-based orders before the first fill.
    # Remaining quantity is derivable only when the submitted quantity is
    # actually present; it is never guessed.
    remaining: Decimal | None = None
    if submitted is not None:
        if submitted < 0:
            raise ResponseContractViolationError(
                "The place response order quantity must not be negative."
            )
        try:
            remaining = derive_quantities(submitted, cumulative).remaining
        except QuantityDerivationError as error:
            raise ResponseContractViolationError(str(error)) from None

    limit_price = _require_positive(
        _optional_decimal_text(
            order.get("price"), "place response order price"
        ),
        "place response order price",
    )
    stop_price = _require_positive(
        _optional_decimal_text(
            order.get("stop_price"), "place response order stop_price"
        ),
        "place response order stop_price",
    )
    average_price = _require_positive(
        _optional_decimal_text(
            order.get("average_price"), "place response order average_price"
        ),
        "place response order average_price",
    )

    created_at = _require_text(
        order.get("created_at"), "place response order created_at"
    )
    last_transaction_at = _optional_text(
        order.get("last_transaction_at"),
        "place response order last_transaction_at",
    )

    return ParsedPlaceResponse(
        server_order_id=_require_text(
            order.get("id"), "place response order id"
        ),
        state=_require_text(order.get("state"), "place response order state"),
        symbol=_require_text(
            order.get("symbol"), "place response order symbol"
        ),
        side=_require_text(order.get("side"), "place response order side"),
        order_type=_require_text(
            order.get("type"), "place response order type"
        ),
        trigger=_require_text(
            order.get("trigger"), "place response order trigger"
        ),
        submitted_quantity=submitted,
        cumulative_quantity=cumulative,
        remaining_quantity=remaining,
        limit_price=limit_price,
        stop_price=stop_price,
        average_fill_price=average_price,
        time_in_force=_require_text(
            order.get("time_in_force"), "place response order time_in_force"
        ),
        market_hours=_require_text(
            order.get("market_hours"), "place response order market_hours"
        ),
        created_at=created_at,
        created_at_utc=_parse_timestamp(created_at),
        last_transaction_at=last_transaction_at,
        last_transaction_at_utc=_parse_timestamp(last_transaction_at),
        execution_count=execution_count,
        # Constant by contract. A place response is evidence of submission
        # and of nothing further, whatever state it happens to carry.
        establishes_submission_only=True,
    )


# --------------------------------------------------------------------------
# Strict cancel-response parsing.
# --------------------------------------------------------------------------

def parse_cancel_response(response: Any) -> ParsedCancelResponse:
    """
    Parse an advertised cancel_equity_order response.

    Envelope (all levels declare ``additionalProperties: false``)::

        {"data": {"accepted": <bool>}, "guide": "..."}

    ``accepted`` must be an actual boolean. Integer 0/1 and the strings
    "true"/"false" are schema violations, not values to coerce: a truthiness
    coercion here would silently manufacture a cancellation conclusion.

    accepted=true means the broker ACCEPTED the cancel REQUEST. Cancellation
    is asynchronous, so this is NOT completion. accepted=false means the
    request was not accepted and must never be converted into a successful
    cancellation.

    In BOTH cases a subsequent get_equity_orders reconciliation is MANDATORY
    to establish the final order state; see ``live_cancel_reconciliation``.
    """
    envelope = _require_mapping(response, "cancel response")
    _require_exact_keys(
        envelope,
        required=frozenset({"data", "guide"}),
        optional=frozenset(),
        label="cancel response",
    )
    # Validated for shape, then dropped. The guide describes reconciliation
    # in prose; the state machine encodes it. Prose is never parsed.
    _require_text(envelope.get("guide"), "cancel response guide")

    data = _require_mapping(envelope.get("data"), "cancel response data")
    _require_exact_keys(
        data,
        required=frozenset({"accepted"}),
        optional=frozenset(),
        label="cancel response data",
    )

    accepted = data.get("accepted")
    if not isinstance(accepted, bool):
        raise ResponseContractViolationError(
            "The cancel response data.accepted must be a boolean; refusing "
            "to coerce a non-boolean into a cancellation conclusion."
        )

    return ParsedCancelResponse(
        acceptance=(
            CancelAcceptance.CANCEL_REQUEST_ACCEPTED_ASYNC
            if accepted
            else CancelAcceptance.CANCEL_REQUEST_NOT_ACCEPTED
        ),
        # Always true. Neither acceptance nor refusal establishes the final
        # order state.
        order_status_reconciliation_required=True,
    )
