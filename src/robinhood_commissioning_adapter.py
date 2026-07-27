"""Narrow injected-session adapter for one-share commissioning.

The normal strategy runner does not import this module. Calls are sequential;
writes have one-shot reservation keys and ambiguous failures are never retried.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from src.live_order_contract import parse_cancel_response, parse_order_status, parse_place_response
from src.one_share_commissioning import CommissioningError


ALLOWED_COMMISSIONING_TOOLS = frozenset({
    "get_accounts", "get_equity_positions", "get_equity_orders",
    "review_equity_order", "place_equity_order", "cancel_equity_order",
})
WRITE_TOOLS = frozenset({"place_equity_order", "cancel_equity_order"})


class AmbiguousWriteError(CommissioningError):
    """A write did not produce a schema-valid result; operator control is required."""


class BrokerRejectedError(CommissioningError):
    """The broker returned an explicit rejection rather than an ambiguous result."""


def assert_tool_allowed(tool_name: Any) -> str:
    if not isinstance(tool_name, str) or tool_name not in ALLOWED_COMMISSIONING_TOOLS:
        raise CommissioningError("tool is outside the commissioning surface")
    return tool_name


def _data(result: Any) -> Mapping[str, Any]:
    if getattr(result, "is_error", False) or getattr(result, "isError", False):
        raise BrokerRejectedError("broker operation was rejected")
    structured = getattr(result, "structuredContent", None)
    value = structured if structured is not None else getattr(result, "data", result)
    if isinstance(value, Mapping) and set(value) in ({"data"}, {"data", "guide"}):
        value = value["data"]
    if not isinstance(value, Mapping):
        raise CommissioningError("broker response is not an object")
    if value.get("next") not in (None, ""):
        raise CommissioningError("pagination is prohibited")
    return value


@dataclass
class RobinhoodCommissioningAdapter:
    session: Any
    call_counts: dict[str, int] = field(default_factory=dict)
    _write_keys: set[str] = field(default_factory=set)
    _write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def __post_init__(self) -> None:
        if self.session is None or not callable(getattr(self.session, "call_tool", None)):
            raise CommissioningError("one injected session is required")

    async def _call(self, tool_name: str, arguments: Mapping[str, Any],
                    *, write_key: str | None = None) -> Mapping[str, Any]:
        allowed = assert_tool_allowed(tool_name)
        if allowed in WRITE_TOOLS:
            if not write_key or write_key in self._write_keys:
                raise CommissioningError("write operation may occur only once")
            self._write_keys.add(write_key)
        self.call_counts[allowed] = self.call_counts.get(allowed, 0) + 1
        async def invoke() -> Mapping[str, Any]:
            try:
                result = await self.session.call_tool(allowed, dict(arguments))
                return _data(result)
            except BrokerRejectedError:
                raise
            except Exception as exc:
                if allowed in WRITE_TOOLS:
                    raise AmbiguousWriteError(
                        "write result is ambiguous; operator required"
                    ) from exc
                raise CommissioningError("read operation failed") from exc

        if allowed in WRITE_TOOLS:
            async with self._write_lock:
                return await invoke()
        return await invoke()

    async def get_accounts(self) -> Mapping[str, Any]:
        return await self._call("get_accounts", {})

    async def get_equity_positions(self, account_number: str) -> Mapping[str, Any]:
        return await self._call("get_equity_positions", {"account_number": account_number})

    async def get_equity_orders(self, account_number: str) -> tuple[Any, ...]:
        data = await self._call("get_equity_orders", {"account_number": account_number})
        rows = data.get("orders")
        if not isinstance(rows, list):
            raise CommissioningError("orders response is not an array")
        try:
            return tuple(parse_order_status(row) for row in rows if row is not None)
        except Exception as exc:
            raise CommissioningError("order status contract rejected") from exc

    async def review_equity_order(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        return await self._call("review_equity_order", arguments)

    async def place_equity_order(self, arguments: Mapping[str, Any], *, role: str) -> Any:
        if role not in {"entry", "stop", "exit"}:
            raise CommissioningError("unknown write role")
        data = await self._call("place_equity_order", arguments, write_key=f"place:{role}")
        try:
            return parse_place_response({"data": data, "guide": "commissioning response"})
        except Exception as exc:
            raise AmbiguousWriteError("place response contract rejected; operator required") from exc

    async def cancel_equity_order(self, arguments: Mapping[str, Any]) -> Any:
        data = await self._call("cancel_equity_order", arguments, write_key="cancel:stop")
        try:
            return parse_cancel_response({"data": data, "guide": "commissioning response"})
        except Exception as exc:
            raise AmbiguousWriteError("cancel response contract rejected; operator required") from exc
