from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from uuid import uuid4


class BrokerValidationError(ValueError):
    """Raised when an order would violate the broker safety rules."""


@dataclass(frozen=True)
class Quote:
    symbol: str
    bid: Decimal
    ask: Decimal

    def validate(self) -> None:
        if self.bid <= 0 or self.ask <= 0:
            raise BrokerValidationError("Bid and ask must be positive.")

        if self.bid > self.ask:
            raise BrokerValidationError("Bid cannot be greater than ask.")


@dataclass(frozen=True)
class BuiltOrder:
    ref_id: str
    tool_arguments: dict[str, Any]


def _money(value: Decimal) -> str:
    """Round an equity price to the nearest cent."""
    return str(
        value.quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
    )


def _validate_common(
    *,
    account_number: str,
    symbol: str,
    quantity: int,
) -> None:
    if not account_number.strip():
        raise BrokerValidationError("Account number is required.")

    if not symbol.strip():
        raise BrokerValidationError("Symbol is required.")

    if quantity < 1:
        raise BrokerValidationError(
            "Quantity must be at least one whole share."
        )


def build_marketable_entry_limit(
    *,
    account_number: str,
    symbol: str,
    quantity: int,
    quote: Quote,
    maximum_slippage_bps: Decimal,
    ref_id: str | None = None,
) -> BuiltOrder:
    """
    Build a price-protected BUY order.

    The limit is placed slightly above the current ask so the order may fill
    quickly, but it cannot fill above the stated limit.
    """

    _validate_common(
        account_number=account_number,
        symbol=symbol,
        quantity=quantity,
    )

    quote.validate()

    if maximum_slippage_bps < 0:
        raise BrokerValidationError(
            "Maximum slippage cannot be negative."
        )

    limit_price = quote.ask * (
        Decimal("1")
        + maximum_slippage_bps / Decimal("10000")
    )

    order_ref = ref_id or str(uuid4())

    arguments = {
        "account_number": account_number,
        "symbol": symbol.upper().strip(),
        "side": "buy",
        "type": "limit",
        "quantity": str(quantity),
        "limit_price": _money(limit_price),
        "time_in_force": "gfd",
        "market_hours": "regular_hours",
        "ref_id": order_ref,
    }

    return BuiltOrder(
        ref_id=order_ref,
        tool_arguments=arguments,
    )


def build_protective_stop_market(
    *,
    account_number: str,
    symbol: str,
    quantity: int,
    stop_price: Decimal,
    ref_id: str | None = None,
) -> BuiltOrder:
    """
    Build a broker-held stop-market SELL order.

    This is the initial protective stop and later may be replaced by the
    break-even stop after price reaches +1R.
    """

    _validate_common(
        account_number=account_number,
        symbol=symbol,
        quantity=quantity,
    )

    if stop_price <= 0:
        raise BrokerValidationError(
            "Stop price must be positive."
        )

    order_ref = ref_id or str(uuid4())

    arguments = {
        "account_number": account_number,
        "symbol": symbol.upper().strip(),
        "side": "sell",
        "type": "stop_market",
        "quantity": str(quantity),
        "stop_price": _money(stop_price),
        "time_in_force": "gfd",
        "market_hours": "regular_hours",
        "ref_id": order_ref,
    }

    return BuiltOrder(
        ref_id=order_ref,
        tool_arguments=arguments,
    )


def build_marketable_exit_limit(
    *,
    account_number: str,
    symbol: str,
    quantity: int,
    quote: Quote,
    maximum_slippage_bps: Decimal,
    ref_id: str | None = None,
) -> BuiltOrder:
    """
    Build a price-protected SELL order.

    The limit is placed slightly below the bid so it may fill quickly, while
    still preventing execution below the stated limit.
    """

    _validate_common(
        account_number=account_number,
        symbol=symbol,
        quantity=quantity,
    )

    quote.validate()

    if maximum_slippage_bps < 0:
        raise BrokerValidationError(
            "Maximum slippage cannot be negative."
        )

    limit_price = quote.bid * (
        Decimal("1")
        - maximum_slippage_bps / Decimal("10000")
    )

    if limit_price <= 0:
        raise BrokerValidationError(
            "Calculated exit limit must be positive."
        )

    order_ref = ref_id or str(uuid4())

    arguments = {
        "account_number": account_number,
        "symbol": symbol.upper().strip(),
        "side": "sell",
        "type": "limit",
        "quantity": str(quantity),
        "limit_price": _money(limit_price),
        "time_in_force": "gfd",
        "market_hours": "regular_hours",
        "ref_id": order_ref,
    }

    return BuiltOrder(
        ref_id=order_ref,
        tool_arguments=arguments,
    )


def review_arguments(order: BuiltOrder) -> dict[str, Any]:
    """
    Return the arguments for review_equity_order.

    Robinhood's review tool accepts the same order fields except ref_id.
    """

    arguments = dict(order.tool_arguments)
    arguments.pop("ref_id", None)
    return arguments


if __name__ == "__main__":
    sample_quote = Quote(
        symbol="AAPL",
        bid=Decimal("203.98"),
        ask=Decimal("204.00"),
    )

    entry = build_marketable_entry_limit(
        account_number="TEST_ACCOUNT",
        symbol="AAPL",
        quantity=10,
        quote=sample_quote,
        maximum_slippage_bps=Decimal("10"),
        ref_id="example-entry-id",
    )

    stop = build_protective_stop_market(
        account_number="TEST_ACCOUNT",
        symbol="AAPL",
        quantity=10,
        stop_price=Decimal("202.00"),
        ref_id="example-stop-id",
    )

    print("Entry review payload:")
    print(review_arguments(entry))

    print("\nEntry placement payload:")
    print(entry.tool_arguments)

    print("\nProtective stop payload:")
    print(stop.tool_arguments)

    print("\nNo Robinhood connection was made.")