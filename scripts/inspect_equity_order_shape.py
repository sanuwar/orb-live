from __future__ import annotations

import asyncio
import getpass
import json
from typing import Any

from src.robinhood_client import (
    RobinhoodClient,
    _unwrap_tool_result,
)


SENSITIVE_KEYS = {
    "account_number",
    "id",
    "order_id",
    "instrument_id",
    "instrument",
    "url",
    "cancel",
    "ref_id",
    "client_order_id",
}


def redact_value(
    key: str,
    value: Any,
) -> Any:
    """
    Preserve the response structure while hiding account,
    order, instrument, and URL identifiers.
    """

    normalized_key = key.lower()

    if normalized_key in SENSITIVE_KEYS:
        if value in {None, ""}:
            return value

        return "<redacted>"

    if isinstance(value, dict):
        return {
            child_key: redact_value(
                child_key,
                child_value,
            )
            for child_key, child_value
            in value.items()
        }

    if isinstance(value, list):
        return [
            redact_value(key, item)
            if not isinstance(item, dict)
            else {
                child_key: redact_value(
                    child_key,
                    child_value,
                )
                for child_key, child_value
                in item.items()
            }
            for item in value
        ]

    return value


def describe_shape(
    value: Any,
) -> Any:
    """
    Show field names and data types without exposing
    brokerage identifiers or private values.
    """

    if isinstance(value, dict):
        return {
            key: describe_shape(child)
            for key, child in value.items()
        }

    if isinstance(value, list):
        if not value:
            return {
                "type": "list",
                "items": "empty",
            }

        return {
            "type": "list",
            "first_item_shape":
                describe_shape(value[0]),
        }

    return {
        "type": type(value).__name__,
        "example": value,
    }


async def main() -> None:
    print(
        "\nEQUITY ORDER RESPONSE-SHAPE "
        "INSPECTOR — READ ONLY\n"
    )

    account_number = getpass.getpass(
        "Enter the full Robinhood account number "
        "(input is hidden): "
    ).strip()

    if not account_number:
        raise SystemExit(
            "Account number is required."
        )

    client = RobinhoodClient()

    async with client.session() as session:
        result = await session.call_tool(
            "get_equity_orders",
            {
                "account_number": account_number,
                "placed_agent": "agentic",
            },
        )

    data = _unwrap_tool_result(
        result,
        "get_equity_orders",
    )

    orders = data.get("orders") or []

    print("\nTOP-LEVEL KEYS")
    print(sorted(data.keys()))

    print(
        f"\nOrders returned on this page: "
        f"{len(orders)}"
    )
    print(
        f"Additional page present: "
        f"{bool(data.get('next'))}"
    )

    if not orders:
        print(
            "\nNo agentic equity orders were returned, "
            "so an individual order shape could not "
            "be inspected."
        )
    else:
        first_order = orders[0]

        print("\nFIRST ORDER — FIELD SHAPE")
        print(
            json.dumps(
                describe_shape(
                    redact_value(
                        "order",
                        first_order,
                    )
                ),
                indent=2,
                sort_keys=True,
                default=str,
            )
        )

        print(
            "\nFIRST ORDER — REDACTED SAMPLE"
        )
        print(
            json.dumps(
                redact_value(
                    "order",
                    first_order,
                ),
                indent=2,
                sort_keys=True,
                default=str,
            )
        )

    print("\nNo write-capable tool was called.")
    print("Broker write capability used: False")


if __name__ == "__main__":
    asyncio.run(main())