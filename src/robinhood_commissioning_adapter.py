"""Narrow injected-session adapter for one-share commissioning.

The normal strategy runner does not import this module. Calls are sequential;
writes have one-shot reservation keys and ambiguous failures are never retried.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
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

    def __init__(self, category: str = "WRITE_RESULT_AMBIGUOUS") -> None:
        self.category = category
        super().__init__(category)


class BrokerRejectedError(CommissioningError):
    """The broker returned an explicit rejection rather than an ambiguous result."""


@dataclass(frozen=True)
class CommissionedAccountRead:
    identity_match: bool
    record_count: int
    eligible_count: int


@dataclass(frozen=True)
class CommissioningPositionRecord:
    symbol: str
    quantity: Decimal


@dataclass(frozen=True)
class CommissioningOrderRecord:
    server_order_id: str
    symbol: str
    side: str
    order_type: str
    state: str
    requested_quantity: Decimal | None
    cumulative_quantity: Decimal
    remaining_quantity: Decimal | None
    stop_price: Decimal | None
    average_fill_price: Decimal | None
    time_in_force: str
    market_hours: str


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


def _decimal_text(value: Any, *, nullable: bool = False) -> Decimal | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise CommissioningError("broker decimal field is not a string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise CommissioningError("broker decimal field is invalid") from exc
    if not parsed.is_finite():
        raise CommissioningError("broker decimal field is non-finite")
    return parsed


@dataclass
class RobinhoodCommissioningAdapter:
    session: Any
    commissioned_account_number: str
    call_counts: dict[str, int] = field(default_factory=dict)
    _write_keys: set[str] = field(default_factory=set)
    _write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def __post_init__(self) -> None:
        if self.session is None or not callable(getattr(self.session, "call_tool", None)):
            raise CommissioningError("one injected session is required")
        if not isinstance(self.commissioned_account_number, str) or not self.commissioned_account_number.strip():
            raise CommissioningError("one commissioned account is required")

    def _validate_order_arguments(self, arguments: Mapping[str, Any], *, role: str) -> None:
        expected = {
            "entry": ("buy", "market", "gfd"),
            "stop": ("sell", "stop_market", "gtc"),
            "exit": ("sell", "market", "gfd"),
        }
        if role not in expected:
            raise CommissioningError("unknown order role")
        allowed_keys = {
            "account_number", "symbol", "side", "type", "quantity",
            "time_in_force", "market_hours",
        }
        if role == "stop":
            allowed_keys.add("stop_price")
        if set(arguments) != allowed_keys:
            raise CommissioningError("order payload fields do not match fixed policy")
        side, order_type, duration = expected[role]
        checks = (
            arguments.get("account_number") == self.commissioned_account_number,
            arguments.get("symbol") == "AAPL",
            arguments.get("side") == side,
            arguments.get("type") == order_type,
            arguments.get("quantity") == "1",
            arguments.get("time_in_force") == duration,
            arguments.get("market_hours") == "regular_hours",
        )
        if not all(checks):
            raise CommissioningError("order payload violates fixed commissioning policy")
        if role == "stop":
            from src.one_share_commissioning import normalize_explicit_price
            normalized = normalize_explicit_price(arguments.get("stop_price"))
            if format(normalized, "f") != arguments.get("stop_price"):
                raise CommissioningError("stop price is not contract-normalized")

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
            except BrokerRejectedError:
                raise
            except Exception as exc:
                if allowed in WRITE_TOOLS:
                    category = (
                        "ENTRY_PLACE_TRANSPORT_AMBIGUOUS"
                        if write_key == "place:entry"
                        else (
                            "STOP_PLACE_TRANSPORT_AMBIGUOUS"
                            if write_key == "place:stop"
                            else "WRITE_RESULT_AMBIGUOUS"
                        )
                    )
                    raise AmbiguousWriteError(category) from exc
                raise CommissioningError("read operation failed") from exc
            try:
                return _data(result)
            except BrokerRejectedError:
                raise
            except Exception as exc:
                if allowed in WRITE_TOOLS:
                    category = (
                        "ENTRY_PLACE_RESPONSE_MALFORMED"
                        if write_key == "place:entry"
                        else (
                            "STOP_PLACE_RESPONSE_MALFORMED"
                            if write_key == "place:stop"
                            else "WRITE_RESULT_AMBIGUOUS"
                        )
                    )
                    raise AmbiguousWriteError(category) from exc
                raise CommissioningError("read operation failed") from exc

        if allowed in WRITE_TOOLS:
            async with self._write_lock:
                return await invoke()
        return await invoke()

    async def get_accounts(self) -> CommissionedAccountRead:
        data = await self._call("get_accounts", {})
        rows = data.get("accounts")
        if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
            raise CommissioningError("accounts response is not a complete array")
        for row in rows:
            if (not isinstance(row.get("account_number"), str)
                    or not isinstance(row.get("state"), str)
                    or not isinstance(row.get("agentic_allowed"), bool)
                    or not isinstance(row.get("deactivated"), bool)
                    or not isinstance(row.get("permanently_deactivated"), bool)):
                raise CommissioningError("account record is schema-incomplete")
        eligible = [
            row for row in rows
            if row.get("agentic_allowed") is True
            and row.get("state") == "active"
            and row.get("deactivated") is not True
            and row.get("permanently_deactivated") is not True
        ]
        if len(eligible) != 1:
            raise CommissioningError("exactly one eligible account is required")
        number = eligible[0].get("account_number")
        if not isinstance(number, str) or number != self.commissioned_account_number:
            raise CommissioningError("commissioned account identity mismatch")
        return CommissionedAccountRead(True, len(rows), 1)

    async def get_equity_positions(self) -> tuple[CommissioningPositionRecord, ...]:
        data = await self._call(
            "get_equity_positions",
            {"account_number": self.commissioned_account_number},
        )
        rows = data.get("positions")
        if not isinstance(rows, list):
            raise CommissioningError("positions response is not an array")
        parsed: list[CommissioningPositionRecord] = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise CommissioningError("position record is not an object")
            symbol = row.get("symbol")
            if not isinstance(symbol, str) or not symbol.strip():
                raise CommissioningError("position symbol is invalid")
            quantity = _decimal_text(row.get("quantity"))
            assert quantity is not None
            parsed.append(CommissioningPositionRecord(symbol.upper(), quantity))
        return tuple(parsed)

    async def get_equity_orders(self) -> tuple[CommissioningOrderRecord, ...]:
        data = await self._call(
            "get_equity_orders",
            {"account_number": self.commissioned_account_number},
        )
        rows = data.get("orders")
        if not isinstance(rows, list):
            raise CommissioningError("orders response is not an array")
        parsed_rows: list[CommissioningOrderRecord] = []
        try:
            for row in rows:
                if not isinstance(row, Mapping):
                    raise CommissioningError("order record is not an object")
                status = parse_order_status(row)
                trigger = str(row["trigger"])
                base_type = str(row["type"])
                displayed_type = f"stop_{base_type}" if trigger == "stop" else base_type
                parsed_rows.append(CommissioningOrderRecord(
                    server_order_id=str(row["id"]), symbol=str(row["symbol"]).upper(),
                    side=str(row["side"]).lower(), order_type=displayed_type.lower(),
                    state=status.state, requested_quantity=status.submitted_quantity,
                    cumulative_quantity=status.cumulative_quantity,
                    remaining_quantity=status.remaining_quantity,
                    stop_price=_decimal_text(row.get("stop_price"), nullable=True),
                    average_fill_price=_decimal_text(row.get("average_price"), nullable=True),
                    time_in_force=str(row["time_in_force"]).lower(),
                    market_hours=str(row["market_hours"]).lower(),
                ))
        except CommissioningError:
            raise
        except Exception as exc:
            raise CommissioningError("order status contract rejected") from exc
        return tuple(parsed_rows)

    async def review_equity_order(self, arguments: Mapping[str, Any], *, role: str) -> Mapping[str, Any]:
        self._validate_order_arguments(arguments, role=role)
        data = await self._call("review_equity_order", arguments)
        expected_side, expected_type, _ = {
            "entry": ("buy", "market", "gfd"),
            "stop": ("sell", "stop_market", "gtc"),
            "exit": ("sell", "market", "gfd"),
        }[role]
        if not all((
            data.get("symbol") == "AAPL", data.get("side") == expected_side,
            data.get("type") == expected_type, data.get("quantity") == "1",
            isinstance(data.get("order_checks"), Mapping),
            isinstance(data.get("quote_data"), Mapping),
        )):
            raise CommissioningError("review response violates fixed commissioning policy")
        if role == "stop" and _decimal_text(data.get("stop_price")) != _decimal_text(
            arguments.get("stop_price")
        ):
            raise CommissioningError("review response stop mismatch")
        return data

    async def place_equity_order(self, arguments: Mapping[str, Any], *, role: str) -> Any:
        self._validate_order_arguments(arguments, role=role)
        data = await self._call("place_equity_order", arguments, write_key=f"place:{role}")
        try:
            parsed = parse_place_response({"data": data, "guide": "commissioning response"})
        except Exception as exc:
            category = (
                "ENTRY_PLACE_RESPONSE_MALFORMED"
                if role == "entry"
                else (
                    "STOP_PLACE_RESPONSE_MALFORMED"
                    if role == "stop"
                    else "WRITE_RESULT_AMBIGUOUS"
                )
            )
            raise AmbiguousWriteError(category) from exc
        expected_side, expected_type, expected_duration = {
            "entry": ("buy", "market", "gfd"),
            "stop": ("sell", "stop_market", "gtc"),
            "exit": ("sell", "market", "gfd"),
        }[role]
        observed_type = (
            f"stop_{parsed.order_type}" if parsed.trigger == "stop" else parsed.order_type
        )
        if not all((
            bool(parsed.server_order_id.strip()), parsed.symbol.upper() == "AAPL",
            parsed.side.lower() == expected_side,
            observed_type.lower() == expected_type,
            parsed.submitted_quantity == Decimal("1"),
            parsed.time_in_force.lower() == expected_duration,
            parsed.market_hours.lower() == "regular_hours",
        )):
            category = (
                "ENTRY_PLACE_RESPONSE_MALFORMED"
                if role == "entry"
                else (
                    "STOP_PLACE_RESPONSE_MALFORMED"
                    if role == "stop"
                    else "WRITE_RESULT_AMBIGUOUS"
                )
            )
            raise AmbiguousWriteError(category)
        if role == "stop" and parsed.stop_price != _decimal_text(arguments["stop_price"]):
            raise AmbiguousWriteError("STOP_PLACE_RESPONSE_MALFORMED")
        return parsed

    async def cancel_equity_order(self, arguments: Mapping[str, Any]) -> Any:
        if set(arguments) != {"order_id"}:
            raise CommissioningError("cancel payload fields do not match fixed policy")
        order_id = arguments.get("order_id")
        if not isinstance(order_id, str) or not order_id.strip():
            raise CommissioningError("cancel requires one transient broker order ID")
        data = await self._call("cancel_equity_order", arguments, write_key="cancel:stop")
        try:
            return parse_cancel_response({"data": data, "guide": "commissioning response"})
        except Exception as exc:
            raise AmbiguousWriteError("cancel response contract rejected; operator required") from exc
