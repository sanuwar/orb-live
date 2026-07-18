from __future__ import annotations

import asyncio
import json
from typing import Any

try:
    from src.robinhood_client import RobinhoodClient
except ImportError:
    from robinhood_client import RobinhoodClient


def schema_value(tool: Any) -> dict[str, Any]:
    schema = getattr(tool, "inputSchema", None)

    if schema is None:
        schema = getattr(tool, "input_schema", None)

    if schema is None:
        return {}

    if isinstance(schema, dict):
        return schema

    if hasattr(schema, "model_dump"):
        return schema.model_dump()

    return {"raw_schema": str(schema)}


async def main() -> None:
    client = RobinhoodClient()

    async with client.session() as session:
        result = await session.list_tools()

    tools = getattr(result, "tools", result)

    matching_tools = sorted(
        (
            tool
            for tool in tools
            if "order" in str(
                getattr(tool, "name", "")
            ).lower()
        ),
        key=lambda tool: str(
            getattr(tool, "name", "")
        ),
    )

    print("\nROBINHOOD MCP ORDER TOOLS\n")

    if not matching_tools:
        print("No tools containing 'order' were found.")
        return

    for tool in matching_tools:
        name = str(getattr(tool, "name", ""))
        description = str(
            getattr(tool, "description", "") or ""
        )

        print("=" * 78)
        print(f"TOOL: {name}")
        print(f"DESCRIPTION: {description}")
        print("INPUT SCHEMA:")

        print(
            json.dumps(
                schema_value(tool),
                indent=2,
                sort_keys=True,
                default=str,
            )
        )

    print("\nNo order tool was called.")
    print("Broker write capability used: False")


if __name__ == "__main__":
    asyncio.run(main())