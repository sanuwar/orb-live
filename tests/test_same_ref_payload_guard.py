"""
Offline tests for the same-ref_id payload integrity guard.

No broker access occurs and no retry policy is exercised. The guard only
answers whether a reused ref_id carries identical order semantics.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from src.same_ref_payload_guard import (
    PayloadNotCanonicalError,
    RefIdPayloadMismatchError,
    SameRefPayloadError,
    SameRefPayloadGuard,
    canonical_order_semantics,
    is_excluded_key,
    order_semantics_digest,
)


REF = "orb-saga-11111111-1111-1111-1111-111111111111"

BASE_PAYLOAD = {
    "account_number": "ACCOUNT-BINDING-A",
    "symbol": "AAPL",
    "side": "buy",
    "type": "limit",
    "quantity": "1",
    "limit_price": "100.00",
    "time_in_force": "gfd",
    "market_hours": "regular_hours",
}


def _payload(**overrides):
    payload = dict(BASE_PAYLOAD)
    payload.update(overrides)
    return payload


# ---------- canonical digest stability ----------

def test_digest_is_stable_across_key_order():
    reversed_payload = dict(reversed(list(BASE_PAYLOAD.items())))
    assert order_semantics_digest(BASE_PAYLOAD) == order_semantics_digest(
        reversed_payload
    )


def test_digest_is_stable_across_equivalent_decimals():
    a = order_semantics_digest(_payload(quantity="1", limit_price="100.00"))
    b = order_semantics_digest(
        _payload(quantity="1.0", limit_price=Decimal("100"))
    )
    c = order_semantics_digest(_payload(quantity=1, limit_price="100.000"))
    assert a == b == c


def test_digest_is_sha256_hex():
    digest = order_semantics_digest(BASE_PAYLOAD)
    assert len(digest) == 64
    assert all(ch in "0123456789abcdef" for ch in digest)


def test_digest_is_deterministic():
    assert order_semantics_digest(BASE_PAYLOAD) == order_semantics_digest(
        dict(BASE_PAYLOAD)
    )


def test_binary_float_in_payload_is_rejected():
    with pytest.raises(PayloadNotCanonicalError):
        order_semantics_digest(_payload(limit_price=100.0))


def test_malformed_numeric_payload_field_is_rejected():
    with pytest.raises(PayloadNotCanonicalError):
        order_semantics_digest(_payload(quantity="not-a-number"))


def test_empty_payload_is_rejected():
    with pytest.raises(PayloadNotCanonicalError):
        order_semantics_digest({})


def test_non_mapping_payload_is_rejected():
    with pytest.raises(PayloadNotCanonicalError):
        order_semantics_digest(["not", "a", "mapping"])


def test_payload_of_only_excluded_keys_is_rejected():
    with pytest.raises(PayloadNotCanonicalError):
        order_semantics_digest({"ref_id": REF, "access_token": "x"})


# ---------- sensitive / transient / response fields are excluded ----------

SENSITIVE_KEYS = (
    "access_token",
    "refresh_token",
    "authorization",
    "api_key",
    "apikey",
    "cookie",
    "bearer_token",
    "client_secret",
    "password",
    "passphrase",
    "session_id",
    "signature",
    "private_key",
)

RESPONSE_KEYS = (
    "id",
    "order_id",
    "state",
    "cumulative_quantity",
    "average_price",
    "created_at",
    "last_transaction_at",
    "executions",
    "fees",
    "instrument_id",
    "placed_agent",
    "trigger",
)

TRANSIENT_KEYS = (
    "attempt",
    "attempts",
    "observed_at",
    "submitted_at",
    "correlation_id",
    "trace_id",
    "request_id",
)


@pytest.mark.parametrize(
    "key", SENSITIVE_KEYS + RESPONSE_KEYS + TRANSIENT_KEYS + ("ref_id",)
)
def test_excluded_keys_are_identified(key):
    assert is_excluded_key(key) is True


@pytest.mark.parametrize(
    "key", ["symbol", "side", "type", "quantity", "limit_price",
            "stop_price", "time_in_force", "market_hours", "account_number"]
)
def test_semantic_keys_are_not_excluded(key):
    assert is_excluded_key(key) is False


@pytest.mark.parametrize(
    "key", SENSITIVE_KEYS + RESPONSE_KEYS + TRANSIENT_KEYS
)
def test_excluded_keys_do_not_change_the_digest(key):
    baseline = order_semantics_digest(BASE_PAYLOAD)
    polluted = order_semantics_digest(_payload(**{key: "ANY-VALUE-XYZ"}))
    assert polluted == baseline


def test_excluded_values_never_appear_in_canonical_form():
    canonical = canonical_order_semantics(
        _payload(access_token="SUPERSECRET", id="ORDER-ID", attempt=3)
    )
    rendered = repr(canonical)
    assert "SUPERSECRET" not in rendered
    assert "ORDER-ID" not in rendered
    assert "access_token" not in canonical
    assert "id" not in canonical
    assert "attempt" not in canonical


def test_nested_excluded_keys_are_dropped():
    canonical = canonical_order_semantics(
        _payload(extra={"api_key": "SECRET", "note": "keep"})
    )
    assert canonical["extra"] == {"note": "keep"}


# ---------- semantic differences are detected ----------

@pytest.mark.parametrize(
    "field, value",
    [
        ("symbol", "MSFT"),
        ("side", "sell"),
        ("type", "market"),
        ("quantity", "2"),
        ("limit_price", "101.00"),
        ("stop_price", "99.00"),
        ("time_in_force", "gtc"),
        ("market_hours", "extended_hours"),
        ("account_number", "ACCOUNT-BINDING-B"),
    ],
)
def test_semantic_change_changes_the_digest(field, value):
    assert order_semantics_digest(
        _payload(**{field: value})
    ) != order_semantics_digest(BASE_PAYLOAD)


def test_unknown_semantic_field_participates_in_the_digest():
    """An unrecognized semantic field must not be silently ignored."""
    assert order_semantics_digest(
        _payload(some_future_order_semantic="x")
    ) != order_semantics_digest(BASE_PAYLOAD)


# ---------- the guard ----------

def test_identical_payload_reuse_is_accepted():
    guard = SameRefPayloadGuard()
    first = guard.register(REF, BASE_PAYLOAD)
    second = guard.register(REF, dict(BASE_PAYLOAD))
    assert first.digest == second.digest
    assert guard.is_bound(REF) is True


def test_equivalent_decimal_reuse_is_accepted():
    guard = SameRefPayloadGuard()
    guard.register(REF, _payload(quantity="1", limit_price="100.00"))
    guard.assert_identical(REF, _payload(quantity="1.0", limit_price="100"))


@pytest.mark.parametrize(
    "field, value",
    [
        ("symbol", "MSFT"),
        ("side", "sell"),
        ("type", "market"),
        ("quantity", "2"),
        ("limit_price", "101.00"),
        ("time_in_force", "gtc"),
        ("market_hours", "extended_hours"),
        ("account_number", "ACCOUNT-BINDING-B"),
    ],
)
def test_mismatched_payload_reuse_is_rejected_locally(field, value):
    guard = SameRefPayloadGuard()
    guard.register(REF, BASE_PAYLOAD)
    with pytest.raises(RefIdPayloadMismatchError):
        guard.register(REF, _payload(**{field: value}))
    with pytest.raises(RefIdPayloadMismatchError):
        guard.assert_identical(REF, _payload(**{field: value}))


def test_excluded_field_change_does_not_trip_the_guard():
    guard = SameRefPayloadGuard()
    guard.register(REF, BASE_PAYLOAD)
    guard.assert_identical(REF, _payload(attempt=7, access_token="NEW"))


def test_unknown_ref_id_fails_closed():
    guard = SameRefPayloadGuard()
    with pytest.raises(RefIdPayloadMismatchError):
        guard.assert_identical("never-registered", BASE_PAYLOAD)


def test_blank_ref_id_is_rejected():
    guard = SameRefPayloadGuard()
    with pytest.raises(SameRefPayloadError):
        guard.register("   ", BASE_PAYLOAD)


def test_distinct_ref_ids_are_independent():
    guard = SameRefPayloadGuard()
    guard.register(REF, BASE_PAYLOAD)
    guard.register("other-ref", _payload(symbol="MSFT"))
    assert guard.is_bound("other-ref") is True


def test_guard_never_stores_the_payload():
    guard = SameRefPayloadGuard()
    guard.register(REF, _payload(access_token="SUPERSECRET"))
    rendered = repr(guard.__dict__)
    assert "SUPERSECRET" not in rendered
    assert "AAPL" not in rendered
    # Only ref_id -> digest is retained.
    assert set(guard._bindings) == {REF}
    assert len(guard._bindings[REF]) == 64


# ---------- documented limits and no broker access ----------

def test_module_documents_the_prohibition():
    source = (
        Path(__file__).parent.parent / "src" / "same_ref_payload_guard.py"
    ).read_text(encoding="utf-8").lower()
    assert "not generally authorized" in source
    assert "ambiguous place request" in source
    assert "prohibited after a known semantic rejection" in source
    assert "has not been verified" in source


def test_module_makes_no_broker_call_and_enables_no_retry():
    source = (
        Path(__file__).parent.parent / "src" / "same_ref_payload_guard.py"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "call_tool",
        "RobinhoodClient",
        "place_equity_order(",
        "time.sleep",
        "asyncio",
        "def retry",
        "max_retries",
    ):
        assert forbidden not in source
