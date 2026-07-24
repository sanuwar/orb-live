"""
Same-ref_id payload integrity guard (LOCAL ONLY; NO BROKER CALL).

PURPOSE AND STRICT LIMITS
-------------------------
Same-ref_id resubmission is NOT generally authorized. This module enables no
retry, no resubmission, and no broker access whatsoever. It only answers one
local question:

    "If a ref_id were ever reused, is the order semantically IDENTICAL?"

A same-ref_id resubmission may be considered LATER, and only for an
AMBIGUOUS place request whose outcome is genuinely unknown, because
place_equity_order advertises idempotent same-ref retry. That advertised
deduplication has NOT been verified.

Same-ref_id resubmission is PROHIBITED after a known semantic rejection, a
validation failure, an account failure, or any known broker rejection. This
module does not and cannot make that determination; it is purely a payload
integrity check that must be satisfied IN ADDITION to a separately
authorized decision.

DIGEST SCOPE
------------
The digest covers ORDER SEMANTICS only. Credentials, token values, transient
local metadata, and broker response fields are excluded by name and by
pattern, and are never stored. The guard retains only ``ref_id -> digest``;
it never retains a payload.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Mapping

try:
    from .broker_action_saga import normalized_decimal_text
except ImportError:  # pragma: no cover - direct-module import fallback
    from broker_action_saga import normalized_decimal_text


class SameRefPayloadError(RuntimeError):
    """Base class for same-ref_id payload integrity violations."""


class PayloadNotCanonicalError(SameRefPayloadError):
    """The payload contains a value that cannot be canonicalized."""


class RefIdPayloadMismatchError(SameRefPayloadError):
    """
    A ref_id was reused with a semantically different payload.

    This fails LOCALLY, before any broker call could be made.
    """


# Excluded exactly: identifiers that are not order semantics, plus broker
# response fields that must never influence the submitted payload identity.
_EXCLUDED_KEYS: frozenset[str] = frozenset(
    {
        # Idempotency key itself: the digest is keyed BY it.
        "ref_id",
        "client_order_id",
        "client_ref_id",
        # Broker response fields (never part of a submitted payload).
        "id",
        "order_id",
        "state",
        "cumulative_quantity",
        "average_price",
        "created_at",
        "updated_at",
        "last_transaction_at",
        "executions",
        "fees",
        "instrument_id",
        "placed_agent",
        "trigger",
        "url",
        "dollar_based_amount",
        # Transient local metadata.
        "attempt",
        "attempts",
        "observed_at",
        "submitted_at",
        "correlation_id",
        "trace_id",
        "request_id",
        "idempotency_scope",
    }
)

# Excluded by substring: credentials and authentication material.
_EXCLUDED_PATTERNS: tuple[str, ...] = (
    "token",
    "secret",
    "credential",
    "password",
    "passphrase",
    "authorization",
    "auth_",
    "_auth",
    "cookie",
    "bearer",
    "session",
    "api_key",
    "apikey",
    "access_",
    "refresh_",
    "signature",
    "private",
)


def is_excluded_key(key: str) -> bool:
    """Whether ``key`` must not participate in the semantic digest."""
    normalized = str(key).strip().lower()

    if normalized in _EXCLUDED_KEYS:
        return True

    return any(pattern in normalized for pattern in _EXCLUDED_PATTERNS)


def _is_numeric_field(key: str) -> bool:
    """Mirrors the existing project rule for numeric payload fields."""
    normalized = str(key).strip().lower()
    return normalized.endswith("quantity") or normalized.endswith("price")


def _canonical_value(key: str, value: Any) -> Any:
    """
    Canonicalize one value without binary floating point.

    Numeric fields are normalized with the existing project decimal rules so
    that "1", "1.0", and Decimal("1.00") are the same semantic quantity.
    """
    if isinstance(value, bool) or value is None:
        return value

    if isinstance(value, Enum):
        value = value.value

    if isinstance(value, Decimal):
        return normalized_decimal_text(value)

    if isinstance(value, float):
        # Reject rather than silently admit binary floating-point error.
        raise PayloadNotCanonicalError(
            f"The payload field {key!r} must not be a binary float."
        )

    if isinstance(value, int):
        if _is_numeric_field(key):
            return normalized_decimal_text(Decimal(value))
        return value

    if isinstance(value, str):
        if _is_numeric_field(key):
            try:
                return normalized_decimal_text(Decimal(value.strip()))
            except InvalidOperation:
                raise PayloadNotCanonicalError(
                    f"The payload field {key!r} is not a valid decimal."
                ) from None
        return value

    if isinstance(value, Mapping):
        return {
            str(item_key): _canonical_value(str(item_key), item_value)
            for item_key, item_value in sorted(
                value.items(), key=lambda pair: str(pair[0])
            )
            if not is_excluded_key(str(item_key))
        }

    if isinstance(value, (list, tuple)):
        return [_canonical_value(key, item) for item in value]

    raise PayloadNotCanonicalError(
        f"The payload field {key!r} has a non-canonical value type."
    )


def canonical_order_semantics(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """
    Reduce a submitted order payload to its canonical SEMANTICS.

    Excluded keys are dropped entirely. Every remaining key participates, so
    an unrecognized semantic field still affects the digest (fail closed
    toward detecting a change rather than ignoring it).
    """
    if not isinstance(payload, Mapping) or not payload:
        raise PayloadNotCanonicalError(
            "The order payload must be a non-empty mapping."
        )

    canonical = {
        str(key): _canonical_value(str(key), value)
        for key, value in sorted(
            payload.items(), key=lambda pair: str(pair[0])
        )
        if not is_excluded_key(str(key))
    }

    if not canonical:
        raise PayloadNotCanonicalError(
            "The order payload contains no semantic fields."
        )

    return canonical


def order_semantics_digest(payload: Mapping[str, Any]) -> str:
    """Stable SHA-256 hex digest of the canonical order semantics."""
    return hashlib.sha256(
        json.dumps(
            canonical_order_semantics(payload),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class RefIdBinding:
    """An immutable binding of a ref_id to its semantic payload digest."""

    ref_id: str
    digest: str


@dataclass
class SameRefPayloadGuard:
    """
    Enforces that a reused ref_id carries an identical semantic payload.

    Stores ONLY ``ref_id -> digest``. It never stores a payload, never
    contacts a broker, and enables no retry policy. Registering a ref_id
    authorizes nothing by itself.
    """

    _bindings: dict[str, str] = field(default_factory=dict)

    def register(
        self, ref_id: str, payload: Mapping[str, Any]
    ) -> RefIdBinding:
        """
        Bind ``ref_id`` to its payload semantics, or verify an existing bind.

        Raises RefIdPayloadMismatchError when the same ref_id is presented
        with different order semantics.
        """
        key = str(ref_id).strip()
        if not key:
            raise SameRefPayloadError("A ref_id is required.")

        digest = order_semantics_digest(payload)
        existing = self._bindings.get(key)

        if existing is None:
            self._bindings[key] = digest
            return RefIdBinding(ref_id=key, digest=digest)

        if existing != digest:
            raise RefIdPayloadMismatchError(
                "This ref_id was already bound to a different order "
                "payload. Reusing a ref_id with changed semantics is "
                "refused locally; no broker call is attempted."
            )

        return RefIdBinding(ref_id=key, digest=existing)

    def assert_identical(
        self, ref_id: str, payload: Mapping[str, Any]
    ) -> None:
        """
        Verify a reuse WITHOUT creating a new binding.

        An unknown ref_id fails closed: there is nothing to compare against.
        """
        key = str(ref_id).strip()
        existing = self._bindings.get(key)

        if existing is None:
            raise RefIdPayloadMismatchError(
                "This ref_id has no recorded payload binding; refusing to "
                "treat it as an identical resubmission."
            )

        if existing != order_semantics_digest(payload):
            raise RefIdPayloadMismatchError(
                "This ref_id was already bound to a different order "
                "payload. Reusing a ref_id with changed semantics is "
                "refused locally; no broker call is attempted."
            )

    def is_bound(self, ref_id: str) -> bool:
        return str(ref_id).strip() in self._bindings
