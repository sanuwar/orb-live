from decimal import Decimal

import pytest

from src.broker_adapter import (
    BrokerValidationError,
    Quote,
    build_marketable_entry_limit,
    build_marketable_exit_limit,
    build_protective_stop_market,
    review_arguments,
)


def valid_quote() -> Quote:
    return Quote(
        symbol="AAPL",
        bid=Decimal("203.98"),
        ask=Decimal("204.00"),
    )


def test_build_marketable_entry_limit():
    order = build_marketable_entry_limit(
        account_number="TEST_ACCOUNT",
        symbol="aapl",
        quantity=10,
        quote=valid_quote(),
        maximum_slippage_bps=Decimal("10"),
        ref_id="entry-123",
    )

    assert order.ref_id == "entry-123"
    assert order.tool_arguments["symbol"] == "AAPL"
    assert order.tool_arguments["side"] == "buy"
    assert order.tool_arguments["type"] == "limit"
    assert order.tool_arguments["quantity"] == "10"
    assert order.tool_arguments["limit_price"] == "204.20"
    assert order.tool_arguments["ref_id"] == "entry-123"


def test_build_protective_stop_market():
    order = build_protective_stop_market(
        account_number="TEST_ACCOUNT",
        symbol="AAPL",
        quantity=10,
        stop_price=Decimal("202"),
        ref_id="stop-123",
    )

    assert order.tool_arguments["side"] == "sell"
    assert order.tool_arguments["type"] == "stop_market"
    assert order.tool_arguments["quantity"] == "10"
    assert order.tool_arguments["stop_price"] == "202.00"
    assert order.tool_arguments["ref_id"] == "stop-123"


def test_build_marketable_exit_limit():
    order = build_marketable_exit_limit(
        account_number="TEST_ACCOUNT",
        symbol="AAPL",
        quantity=10,
        quote=valid_quote(),
        maximum_slippage_bps=Decimal("10"),
        ref_id="exit-123",
    )

    assert order.tool_arguments["side"] == "sell"
    assert order.tool_arguments["type"] == "limit"
    assert order.tool_arguments["limit_price"] == "203.78"
    assert order.tool_arguments["ref_id"] == "exit-123"


def test_review_arguments_removes_ref_id():
    order = build_marketable_entry_limit(
        account_number="TEST_ACCOUNT",
        symbol="AAPL",
        quantity=10,
        quote=valid_quote(),
        maximum_slippage_bps=Decimal("10"),
        ref_id="entry-123",
    )

    arguments = review_arguments(order)

    assert "ref_id" not in arguments
    assert arguments["type"] == "limit"
    assert arguments["quantity"] == "10"


def test_invalid_quote_is_rejected():
    quote = Quote(
        symbol="AAPL",
        bid=Decimal("205"),
        ask=Decimal("204"),
    )

    with pytest.raises(
        BrokerValidationError,
        match="Bid cannot be greater",
    ):
        build_marketable_entry_limit(
            account_number="TEST_ACCOUNT",
            symbol="AAPL",
            quantity=10,
            quote=quote,
            maximum_slippage_bps=Decimal("10"),
        )


def test_zero_quantity_is_rejected():
    with pytest.raises(
        BrokerValidationError,
        match="at least one whole share",
    ):
        build_protective_stop_market(
            account_number="TEST_ACCOUNT",
            symbol="AAPL",
            quantity=0,
            stop_price=Decimal("202"),
        )


def test_negative_slippage_is_rejected():
    with pytest.raises(
        BrokerValidationError,
        match="cannot be negative",
    ):
        build_marketable_entry_limit(
            account_number="TEST_ACCOUNT",
            symbol="AAPL",
            quantity=10,
            quote=valid_quote(),
            maximum_slippage_bps=Decimal("-1"),
        )