from __future__ import annotations

from contextlib import asynccontextmanager
from decimal import Decimal

import pytest

from src.order_observer import (
    EquityOrderObserver,
    InvalidOrderResponseError,
    OrderNotFoundError,
    OrderObservationError,
    mask_identifier,
    parse_observed_order,
)


class FakeResult:
    def __init__(self, data: dict):
        self.isError = False
        self.structuredContent = {
            "data": data,
        }
        self.content = []


class FakeSession:
    def __init__(self, data: dict):
        self.data = data
        self.calls: list[
            tuple[str, dict]
        ] = []

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict,
    ):
        self.calls.append(
            (tool_name, arguments)
        )

        return FakeResult(self.data)


class FakeClient:
    def __init__(self, data: dict):
        self.fake_session = FakeSession(
            data
        )

    @asynccontextmanager
    async def session(self):
        yield self.fake_session


def filled_order(
    *,
    order_id: str = "order-123456",
) -> dict:
    return {
        "id": order_id,
        "symbol": "AAPL",
        "side": "buy",
        "type": "limit",
        "state": "filled",
        "quantity": "2.000000",
        "cumulative_quantity": "2.000000",
        "price": "100.500000",
        "stop_price": None,
        "trigger": "immediate",
        "placed_agent": "agentic",
        "average_price": "100.250000",
        "fees": "0.040000",
        "created_at": (
            "2026-07-17T13:47:42.010026Z"
        ),
        "last_transaction_at": (
            "2026-07-17T13:47:42.130000Z"
        ),
        "market_hours": "regular_hours",
        "time_in_force": "gfd",
        "executions": [
            {
                "id": "execution-1",
                "price": "100.000000",
                "quantity": "1.000000",
                "fees": "0.020000",
                "timestamp": (
                    "2026-07-17T13:47:42.100000Z"
                ),
            },
            {
                "id": "execution-2",
                "price": "100.500000",
                "quantity": "1.000000",
                "fees": "0.020000",
                "timestamp": (
                    "2026-07-17T13:47:42.130000Z"
                ),
            },
        ],
    }


def pending_order() -> dict:
    return {
        "id": "order-pending",
        "symbol": "MSFT",
        "side": "buy",
        "type": "limit",
        "state": "confirmed",
        "quantity": "3",
        "cumulative_quantity": "0",
        "price": "500",
        "stop_price": None,
        "trigger": "immediate",
        "placed_agent": "agentic",
        "average_price": None,
        "fees": "0",
        "created_at": (
            "2026-07-17T14:00:00Z"
        ),
        "last_transaction_at": None,
        "market_hours": "regular_hours",
        "time_in_force": "gfd",
        "executions": [],
    }


def test_parse_filled_order_and_executions():
    observed = parse_observed_order(
        filled_order()
    )

    assert observed.order_id == (
        "order-123456"
    )
    assert observed.symbol == "AAPL"
    assert observed.state == "filled"

    assert (
        observed.requested_quantity
        == Decimal("2.000000")
    )
    assert (
        observed.filled_quantity
        == Decimal("2.000000")
    )
    assert (
        observed.average_price
        == Decimal("100.250000")
    )
    assert observed.fees == Decimal(
        "0.040000"
    )

    assert len(observed.executions) == 2
    assert (
        observed.execution_quantity
        == Decimal("2.000000")
    )
    assert (
        observed.execution_fees
        == Decimal("0.040000")
    )
    assert (
        observed.calculated_average_price
        == Decimal("100.250000")
    )

    assert observed.has_any_fill is True
    assert observed.is_agentic_order is True

    assert observed.created_at is not None
    assert (
        observed.created_at.utcoffset()
        is not None
    )


def test_parse_pending_unfilled_order():
    observed = parse_observed_order(
        pending_order()
    )

    assert observed.state == "confirmed"
    assert observed.filled_quantity == Decimal(
        "0"
    )
    assert observed.average_price is None
    assert observed.executions == ()
    assert observed.execution_quantity == Decimal(
        "0"
    )
    assert (
        observed.calculated_average_price
        is None
    )
    assert observed.has_any_fill is False


def test_missing_order_id_is_rejected():
    raw = filled_order()
    raw["id"] = ""

    with pytest.raises(
        InvalidOrderResponseError,
        match="no order ID",
    ):
        parse_observed_order(raw)


def test_filled_quantity_requires_average_price():
    raw = filled_order()
    raw["average_price"] = None

    with pytest.raises(
        InvalidOrderResponseError,
        match="without an average fill price",
    ):
        parse_observed_order(raw)


def test_executions_with_zero_cumulative_quantity_are_rejected():
    raw = filled_order()
    raw["state"] = "confirmed"
    raw["cumulative_quantity"] = "0"
    raw["average_price"] = None

    with pytest.raises(
        InvalidOrderResponseError,
        match="executions",
    ):
        parse_observed_order(raw)


def test_invalid_execution_price_is_rejected():
    raw = filled_order()
    raw["executions"][0]["price"] = "invalid"

    with pytest.raises(
        InvalidOrderResponseError,
        match="execution.price",
    ):
        parse_observed_order(raw)


def test_invalid_timestamp_is_rejected():
    raw = filled_order()
    raw["created_at"] = "not-a-timestamp"

    with pytest.raises(
        InvalidOrderResponseError,
        match="created_at",
    ):
        parse_observed_order(raw)


def test_mask_identifier():
    assert (
        mask_identifier(
            "1234567890ABCDEF"
        )
        == "••••••ABCDEF"
    )

    assert (
        mask_identifier("short")
        == "••••••"
    )


@pytest.mark.anyio
async def test_observe_single_order():
    client = FakeClient(
        {
            "orders": [
                filled_order()
            ]
        }
    )

    observer = EquityOrderObserver(
        client=client
    )

    observed = await observer.observe(
        account_number="TEST12345678",
        order_id="order-123456",
    )

    assert observed.symbol == "AAPL"
    assert observed.state == "filled"

    assert client.fake_session.calls == [
        (
            "get_equity_orders",
            {
                "account_number":
                    "TEST12345678",
                "order_id":
                    "order-123456",
            },
        )
    ]


@pytest.mark.anyio
async def test_account_number_is_required():
    observer = EquityOrderObserver(
        client=FakeClient(
            {"orders": []}
        )
    )

    with pytest.raises(
        OrderObservationError,
        match="Account number is required",
    ):
        await observer.observe(
            account_number="",
            order_id="order-123456",
        )


@pytest.mark.anyio
async def test_order_id_is_required():
    observer = EquityOrderObserver(
        client=FakeClient(
            {"orders": []}
        )
    )

    with pytest.raises(
        OrderObservationError,
        match="Order ID is required",
    ):
        await observer.observe(
            account_number="TEST12345678",
            order_id="",
        )


@pytest.mark.anyio
async def test_order_not_found():
    observer = EquityOrderObserver(
        client=FakeClient(
            {"orders": []}
        )
    )

    with pytest.raises(
        OrderNotFoundError,
        match="was not found",
    ):
        await observer.observe(
            account_number="TEST12345678",
            order_id="missing-order",
        )


@pytest.mark.anyio
async def test_multiple_orders_fail_closed():
    observer = EquityOrderObserver(
        client=FakeClient(
            {
                "orders": [
                    filled_order(
                        order_id="order-1"
                    ),
                    filled_order(
                        order_id="order-2"
                    ),
                ]
            }
        )
    )

    with pytest.raises(
        InvalidOrderResponseError,
        match="more than one order",
    ):
        await observer.observe(
            account_number="TEST12345678",
            order_id="order-1",
        )


@pytest.mark.anyio
async def test_response_order_id_must_match_request():
    observer = EquityOrderObserver(
        client=FakeClient(
            {
                "orders": [
                    filled_order(
                        order_id="different-order"
                    )
                ]
            }
        )
    )

    with pytest.raises(
        InvalidOrderResponseError,
        match="does not match",
    ):
        await observer.observe(
            account_number="TEST12345678",
            order_id="requested-order",
        )


@pytest.mark.anyio
async def test_single_order_lookup_rejects_pagination():
    observer = EquityOrderObserver(
        client=FakeClient(
            {
                "orders": [
                    filled_order()
                ],
                "next": (
                    "https://example.invalid/"
                    "next-page"
                ),
            }
        )
    )

    with pytest.raises(
        InvalidOrderResponseError,
        match="another page",
    ):
        await observer.observe(
            account_number="TEST12345678",
            order_id="order-123456",
        )


@pytest.mark.anyio
async def test_orders_field_must_be_a_list():
    observer = EquityOrderObserver(
        client=FakeClient(
            {
                "orders": {
                    "id": "invalid-shape"
                }
            }
        )
    )

    with pytest.raises(
        InvalidOrderResponseError,
        match="valid orders list",
    ):
        await observer.observe(
            account_number="TEST12345678",
            order_id="order-123456",
        )