from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

try:
    from .account_state import (
        EquityOrder,
        parse_order,
    )
    from .robinhood_client import (
        RobinhoodClient,
        RobinhoodClientError,
        _unwrap_tool_result,
    )
except ImportError:
    from account_state import (
        EquityOrder,
        parse_order,
    )
    from robinhood_client import (
        RobinhoodClient,
        RobinhoodClientError,
        _unwrap_tool_result,
    )


class OrderObservationError(
    RobinhoodClientError
):
    pass


class OrderNotFoundError(
    OrderObservationError
):
    pass


class InvalidOrderResponseError(
    OrderObservationError
):
    pass


@dataclass(frozen=True)
class OrderExecution:
    execution_id: str
    price: Decimal
    quantity: Decimal
    fees: Decimal
    timestamp: datetime | None


@dataclass(frozen=True)
class ObservedEquityOrder:
    order: EquityOrder
    average_price: Decimal | None
    fees: Decimal
    created_at: datetime | None
    last_transaction_at: datetime | None
    market_hours: str
    time_in_force: str
    executions: tuple[
        OrderExecution,
        ...,
    ]

    @property
    def order_id(self) -> str:
        return self.order.order_id

    @property
    def symbol(self) -> str:
        return self.order.symbol

    @property
    def state(self) -> str:
        return self.order.state

    @property
    def requested_quantity(
        self,
    ) -> Decimal | None:
        return self.order.quantity

    @property
    def filled_quantity(self) -> Decimal:
        return self.order.cumulative_quantity

    @property
    def has_any_fill(self) -> bool:
        return self.filled_quantity > 0

    @property
    def execution_quantity(self) -> Decimal:
        return sum(
            (
                execution.quantity
                for execution in self.executions
            ),
            Decimal("0"),
        )

    @property
    def execution_fees(self) -> Decimal:
        return sum(
            (
                execution.fees
                for execution in self.executions
            ),
            Decimal("0"),
        )

    @property
    def calculated_average_price(
        self,
    ) -> Decimal | None:
        quantity = self.execution_quantity

        if quantity <= 0:
            return None

        total_value = sum(
            (
                execution.price
                * execution.quantity
                for execution in self.executions
            ),
            Decimal("0"),
        )

        return total_value / quantity

    @property
    def is_agentic_order(self) -> bool:
        return (
            self.order.placed_agent
            == "agentic"
        )


def required_decimal(
    value: Any,
    *,
    field_name: str,
    allow_zero: bool = True,
) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (
        InvalidOperation,
        TypeError,
        ValueError,
    ) as error:
        raise InvalidOrderResponseError(
            f"Broker order field "
            f"{field_name!r} is not a valid "
            "decimal value."
        ) from error

    if parsed < 0:
        raise InvalidOrderResponseError(
            f"Broker order field "
            f"{field_name!r} cannot be negative."
        )

    if not allow_zero and parsed <= 0:
        raise InvalidOrderResponseError(
            f"Broker order field "
            f"{field_name!r} must be positive."
        )

    return parsed


def optional_decimal(
    value: Any,
    *,
    field_name: str,
    allow_zero: bool = True,
) -> Decimal | None:
    if value in {None, ""}:
        return None

    return required_decimal(
        value,
        field_name=field_name,
        allow_zero=allow_zero,
    )


def optional_datetime(
    value: Any,
    *,
    field_name: str,
) -> datetime | None:
    if value in {None, ""}:
        return None

    raw = str(value).strip()

    if raw.endswith("Z"):
        raw = (
            raw[:-1]
            + "+00:00"
        )

    try:
        return datetime.fromisoformat(raw)
    except ValueError as error:
        raise InvalidOrderResponseError(
            f"Broker order field "
            f"{field_name!r} is not a valid "
            "ISO-8601 timestamp."
        ) from error


def parse_execution(
    raw: dict[str, Any],
) -> OrderExecution:
    if not isinstance(raw, dict):
        raise InvalidOrderResponseError(
            "Broker execution entry is not "
            "an object."
        )

    return OrderExecution(
        execution_id=str(
            raw.get("id", "")
        ),
        price=required_decimal(
            raw.get("price"),
            field_name="execution.price",
            allow_zero=False,
        ),
        quantity=required_decimal(
            raw.get("quantity"),
            field_name="execution.quantity",
            allow_zero=False,
        ),
        fees=required_decimal(
            raw.get("fees", "0"),
            field_name="execution.fees",
        ),
        timestamp=optional_datetime(
            raw.get("timestamp"),
            field_name="execution.timestamp",
        ),
    )


def parse_observed_order(
    raw: dict[str, Any],
) -> ObservedEquityOrder:
    if not isinstance(raw, dict):
        raise InvalidOrderResponseError(
            "Broker order response item is not "
            "an object."
        )

    order = parse_order(raw)

    if not order.order_id:
        raise InvalidOrderResponseError(
            "Broker order response has no order ID."
        )

    if not order.symbol:
        raise InvalidOrderResponseError(
            "Broker order response has no symbol."
        )

    executions_raw = (
        raw.get("executions")
        or []
    )

    if not isinstance(executions_raw, list):
        raise InvalidOrderResponseError(
            "Broker order executions field is "
            "not a list."
        )

    executions = tuple(
        parse_execution(execution)
        for execution in executions_raw
    )

    average_price = optional_decimal(
        raw.get("average_price"),
        field_name="average_price",
        allow_zero=False,
    )

    if (
        order.cumulative_quantity > 0
        and average_price is None
    ):
        raise InvalidOrderResponseError(
            "Broker reports a filled quantity "
            "without an average fill price."
        )

    if (
        order.cumulative_quantity == 0
        and executions
    ):
        raise InvalidOrderResponseError(
            "Broker reports executions while the "
            "cumulative filled quantity is zero."
        )

    return ObservedEquityOrder(
        order=order,
        average_price=average_price,
        fees=required_decimal(
            raw.get("fees", "0"),
            field_name="fees",
        ),
        created_at=optional_datetime(
            raw.get("created_at"),
            field_name="created_at",
        ),
        last_transaction_at=(
            optional_datetime(
                raw.get(
                    "last_transaction_at"
                ),
                field_name=(
                    "last_transaction_at"
                ),
            )
        ),
        market_hours=str(
            raw.get("market_hours", "")
        ),
        time_in_force=str(
            raw.get("time_in_force", "")
        ),
        executions=executions,
    )


def mask_identifier(
    value: str,
) -> str:
    cleaned = value.strip()

    if len(cleaned) <= 6:
        return "••••••"

    return f"••••••{cleaned[-6:]}"


class EquityOrderObserver:
    """
    Reads one equity order by account number and
    broker order ID.

    The caller must supply both identifiers explicitly.
    This observer does not call get_accounts and cannot
    review, place, replace, or cancel an order.
    """

    def __init__(
        self,
        client: RobinhoodClient | None = None,
    ):
        self.client = client or RobinhoodClient()

    async def observe(
        self,
        *,
        account_number: str,
        order_id: str,
    ) -> ObservedEquityOrder:
        account_number = (
            account_number.strip()
        )
        order_id = order_id.strip()

        if not account_number:
            raise OrderObservationError(
                "Account number is required."
            )

        if not order_id:
            raise OrderObservationError(
                "Order ID is required."
            )

        async with self.client.session() as session:
            result = await session.call_tool(
                "get_equity_orders",
                {
                    "account_number":
                        account_number,
                    "order_id": order_id,
                },
            )

        data = _unwrap_tool_result(
            result,
            "get_equity_orders",
        )

        if data.get("next"):
            raise InvalidOrderResponseError(
                "Single-order lookup unexpectedly "
                "returned another page."
            )

        orders = data.get("orders") or []

        if not isinstance(orders, list):
            raise InvalidOrderResponseError(
                "Broker order response does not "
                "contain a valid orders list."
            )

        if not orders:
            raise OrderNotFoundError(
                "The requested order was not found "
                "in the supplied account."
            )

        if len(orders) != 1:
            raise InvalidOrderResponseError(
                "Single-order lookup returned more "
                "than one order."
            )

        observed = parse_observed_order(
            orders[0]
        )

        if observed.order_id != order_id:
            raise InvalidOrderResponseError(
                "The broker response order ID does "
                "not match the requested order ID."
            )

        return observed


def print_observation(
    observed: ObservedEquityOrder,
) -> None:
    print(
        "\nEQUITY ORDER OBSERVATION — "
        "READ ONLY\n"
    )

    print(
        f"Order: "
        f"{mask_identifier(observed.order_id)}"
    )
    print(f"Symbol: {observed.symbol}")
    print(f"Side: {observed.order.side}")
    print(
        f"Type: "
        f"{observed.order.order_type}"
    )
    print(f"State: {observed.state}")
    print(
        f"Requested quantity: "
        f"{observed.requested_quantity}"
    )
    print(
        f"Filled quantity: "
        f"{observed.filled_quantity}"
    )
    print(
        f"Average fill price: "
        f"{observed.average_price}"
    )
    print(f"Fees: {observed.fees}")
    print(
        f"Execution count: "
        f"{len(observed.executions)}"
    )
    print(
        f"Execution quantity: "
        f"{observed.execution_quantity}"
    )
    print(
        f"Execution fees: "
        f"{observed.execution_fees}"
    )
    print(
        f"Placed by agentic MCP: "
        f"{observed.is_agentic_order}"
    )

    print(
        "\nNo write-capable Robinhood tool "
        "was called."
    )


def main() -> None:
    print(
        "\nEQUITY ORDER OBSERVER\n"
    )
    print(
        "Reads one historical or open equity "
        "order using an explicitly supplied "
        "account number and order ID."
    )
    print(
        "It parses broker-confirmed average price, "
        "filled quantity, executions, fees, and "
        "timestamps."
    )
    print(
        "Broker write capability: False"
    )


if __name__ == "__main__":
    main()