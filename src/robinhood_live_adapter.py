"""
Default-off Robinhood adapter boundary for the initialization checkpoint.

This module performs no I/O at import time, constructs no client or session,
and exposes no executor transport.  A caller must supply one already-open MCP
session and the exact initialization-only mode.  Only four named methods can
delegate, once each and in a fixed order.  Place/cancel response parsers remain
schema- and fixture-validated; this checkpoint does not live-validate either
write response parser.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Callable, Mapping

from .account_state import parse_position, select_agentic_account
from .broker_identity import CommissionedAccount, load_local_override
from .broker_snapshot import account_fingerprint
from .config import load_config
from .live_order_contract import (
    ResponseContractViolationError,
    parse_cancel_response,
    parse_order_status,
    parse_place_response,
)
from .live_order_state_model import (
    ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES,
    OrderStateClass,
    classify_order_state,
)


INITIALIZATION_TOOL_SEQUENCE: tuple[str, ...] = (
    "get_accounts",
    "get_equity_positions",
    "get_equity_orders",
    "review_equity_order",
)
MAX_MCP_SESSIONS = 1
MAX_DELEGATED_TOOL_CALLS = 4
MAX_PAGINATION_CALLS = 0
MAX_RETRIES = 0

# Fixture-only parser wiring. Neither callable is invoked by the live sequence.
PLACE_RESPONSE_PARSER = parse_place_response
CANCEL_RESPONSE_PARSER = parse_cancel_response

FIXED_REVIEW_ARGUMENTS: Mapping[str, str] = {
    "symbol": "AAPL",
    "side": "buy",
    "type": "market",
    "quantity": "1",
    "market_hours": "regular_hours",
    "time_in_force": "gfd",
}


class LiveAdapterMode(str, Enum):
    INITIALIZATION_READ_REVIEW_ONLY = "INITIALIZATION_READ_REVIEW_ONLY"


class LiveAdapterInitializationError(RuntimeError):
    """Base class for fixed, non-sensitive initialization failures."""


class InvalidLiveAdapterModeError(LiveAdapterInitializationError):
    pass


class ProhibitedToolError(LiveAdapterInitializationError):
    pass


class CallSequenceError(LiveAdapterInitializationError):
    pass


class SessionLimitViolationError(LiveAdapterInitializationError):
    pass


class ResponseShapeViolationError(LiveAdapterInitializationError):
    pass


class BrowserAuthenticationRequestError(LiveAdapterInitializationError):
    pass


class CommissionedIdentityMismatchError(LiveAdapterInitializationError):
    pass


@dataclass(frozen=True)
class AccountInitializationSummary:
    record_count: int
    eligible_account_count: int
    identity_match: bool
    parser_succeeded: bool = True


@dataclass(frozen=True)
class PositionInitializationSummary:
    record_count: int
    null_record_count: int
    parser_succeeded: bool = True
    pagination_present: bool = False


@dataclass(frozen=True)
class OrderInitializationSummary:
    record_count: int
    null_record_count: int
    lifecycle_counts: tuple[tuple[str, int], ...]
    parser_succeeded: bool = True
    pagination_present: bool = False
    active_protection_mapping_empty: bool = True
    historical_records_prove_active_protection: bool = False


@dataclass(frozen=True)
class ReviewInitializationSummary:
    field_paths: tuple[str, ...]
    json_types: tuple[tuple[str, str], ...]
    boolean_count: int
    object_count: int
    array_count: int
    parser_succeeded: bool = True
    non_committing: bool = True
    place_parser_live_validated: bool = False
    cancel_parser_live_validated: bool = False


@dataclass(frozen=True)
class InitializationSequenceSummary:
    tool_names: tuple[str, ...]
    delegated_call_count: int
    session_count: int
    complete: bool


_ACCOUNT_REQUIRED = frozenset(
    {
        "account_number",
        "rhs_account_number",
        "type",
        "brokerage_account_type",
        "is_default",
        "agentic_allowed",
        "option_level",
        "state",
        "deactivated",
        "permanently_deactivated",
    }
)
_ACCOUNT_OPTIONAL = frozenset(
    {"rhc_account_number", "nickname", "management_type", "affiliate"}
)
_POSITION_REQUIRED = frozenset(
    {
        "symbol",
        "quantity",
        "intraday_quantity",
        "shares_available_for_sells",
        "shares_held_for_sells",
        "shares_held_for_stock_grants",
        "shares_held_for_options_events",
        "shares_held_for_asset_transfer",
        "shares_pending_from_options_events",
        "type",
    }
)
_POSITION_OPTIONAL = frozenset({"average_buy_price"})
_REVIEW_REQUIRED = frozenset(
    {"symbol", "side", "type", "order_checks", "quote_data"}
)
_REVIEW_OPTIONAL = frozenset(
    {"quantity", "dollar_amount", "limit_price", "stop_price", "market_data_disclosure"}
)
_QUOTE_REQUIRED = frozenset(
    {
        "symbol",
        "last_trade_price",
        "venue_last_trade_time",
        "last_non_reg_trade_price",
        "venue_last_non_reg_trade_time",
        "adjusted_previous_close",
        "previous_close",
        "previous_close_date",
        "bid_price",
        "venue_bid_time",
        "ask_price",
        "venue_ask_time",
        "has_traded",
        "state",
    }
)
_QUOTE_NULLABLE = frozenset(
    {"last_non_reg_trade_price", "venue_last_non_reg_trade_time", "previous_close_date"}
)


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ResponseShapeViolationError(f"{label} must be an object.")
    return value


def _exact_keys(
    value: Mapping[str, Any],
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
    label: str,
) -> None:
    keys = set(value)
    if not all(isinstance(key, str) for key in keys):
        raise ResponseShapeViolationError(f"{label} contains a non-string field name.")
    if required - keys:
        raise ResponseShapeViolationError(f"{label} is missing a required field.")
    if keys - required - optional:
        raise ResponseShapeViolationError(f"{label} contains an unexpected field.")


def _text(value: Any, label: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise ResponseShapeViolationError(f"{label} must be a string.")
    return value


def _boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ResponseShapeViolationError(f"{label} must be a boolean.")
    return value


def _decimal_text(value: Any, label: str, *, nullable: bool = False) -> Decimal | None:
    text = _text(value, label, nullable=nullable)
    if text is None:
        return None
    try:
        parsed = Decimal(text)
    except InvalidOperation:
        raise ResponseShapeViolationError(f"{label} must be a decimal string.") from None
    if not parsed.is_finite():
        raise ResponseShapeViolationError(f"{label} must be a finite decimal string.")
    return parsed


def _array_or_null(value: Any, label: str) -> tuple[Any, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ResponseShapeViolationError(f"{label} must be an array or null.")
    return tuple(value)


def _unwrap(result: Any, tool_name: str) -> Mapping[str, Any]:
    if getattr(result, "isError", False) is not False:
        raise ResponseShapeViolationError(f"{tool_name} did not return a success response.")
    content = getattr(result, "structuredContent", None)
    envelope = _mapping(content, f"{tool_name} response")
    _exact_keys(
        envelope,
        required=frozenset({"data", "guide"}),
        label=f"{tool_name} response",
    )
    _text(envelope.get("guide"), f"{tool_name} response guide")
    return _mapping(envelope.get("data"), f"{tool_name} response data")


def _validate_account(raw: Any) -> Mapping[str, Any]:
    account = _mapping(raw, "account record")
    _exact_keys(account, required=_ACCOUNT_REQUIRED, optional=_ACCOUNT_OPTIONAL, label="account record")
    for field in (
        "account_number",
        "rhs_account_number",
        "type",
        "brokerage_account_type",
        "option_level",
        "state",
    ):
        _text(account.get(field), f"account {field}")
    for field in ("is_default", "agentic_allowed", "deactivated", "permanently_deactivated"):
        _boolean(account.get(field), f"account {field}")
    for field in _ACCOUNT_OPTIONAL:
        if field in account:
            _text(account.get(field), f"account {field}")
    if not str(account["account_number"]).strip():
        raise ResponseShapeViolationError("account account_number must not be blank.")
    return account


def _validate_position(raw: Any) -> None:
    position = _mapping(raw, "position record")
    _exact_keys(position, required=_POSITION_REQUIRED, optional=_POSITION_OPTIONAL, label="position record")
    _text(position.get("symbol"), "position symbol")
    _text(position.get("type"), "position type")
    for field in _POSITION_REQUIRED - {"symbol", "type"}:
        _decimal_text(position.get(field), f"position {field}")
    if "average_buy_price" in position:
        _decimal_text(position.get("average_buy_price"), "position average_buy_price", nullable=True)
    # Existing normalized position parser is invoked only after strict shape validation.
    parse_position(dict(position))


def _pagination_absent(data: Mapping[str, Any], tool_name: str) -> None:
    if "next" not in data:
        return
    next_value = data.get("next")
    if not isinstance(next_value, str):
        raise ResponseShapeViolationError(f"{tool_name} next must be a string.")
    if next_value:
        raise ResponseShapeViolationError(f"{tool_name} pagination is prohibited.")


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, list):
        return "array"
    raise ResponseShapeViolationError("Review response contains a non-JSON value.")


def _shape(value: Any, path: str, rows: list[tuple[str, str]]) -> tuple[int, int, int]:
    kind = _json_type(value)
    rows.append((path, kind))
    booleans = int(kind == "boolean")
    objects = int(kind == "object")
    arrays = int(kind == "array")
    if isinstance(value, Mapping):
        for key in sorted(value):
            if not isinstance(key, str) or not key:
                raise ResponseShapeViolationError("Review response has an invalid field name.")
            child = f"{path}.{key}"
            b, o, a = _shape(value[key], child, rows)
            booleans += b
            objects += o
            arrays += a
    elif isinstance(value, list):
        for item in value:
            b, o, a = _shape(item, f"{path}[]", rows)
            booleans += b
            objects += o
            arrays += a
    return booleans, objects, arrays


class RobinhoodLiveAdapter:
    """
    Initialization-only sequence controller over one injected MCP session.

    It intentionally does not implement ``BrokerTransport`` and exposes no
    executor-compatible methods or ``simulated_only`` marker.
    """

    __slots__ = (
        "_mode",
        "_session",
        "_session_attached",
        "_next_index",
        "_account_number",
        "_config_loader",
        "_local_config_loader",
    )

    def __init__(
        self,
        mode: LiveAdapterMode | None = None,
        *,
        config_loader: Callable[[], Mapping[str, Any]] = load_config,
        local_config_loader: Callable[[], Mapping[str, Any]] = load_local_override,
    ) -> None:
        if mode is not LiveAdapterMode.INITIALIZATION_READ_REVIEW_ONLY:
            raise InvalidLiveAdapterModeError("The exact initialization-only adapter mode is required.")
        self._mode = mode
        self._session: Any | None = None
        self._session_attached = False
        self._next_index = 0
        self._account_number: str | None = None
        self._config_loader = config_loader
        self._local_config_loader = local_config_loader

    def __repr__(self) -> str:
        return "RobinhoodLiveAdapter(mode=INITIALIZATION_READ_REVIEW_ONLY)"

    def attach_session(self, session: Any) -> None:
        if self._session_attached:
            raise SessionLimitViolationError("The one-session limit has already been consumed.")
        if session is None or not callable(getattr(session, "call_tool", None)):
            raise SessionLimitViolationError("Exactly one usable injected MCP session is required.")
        self._session = session
        self._session_attached = True

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        raise ProhibitedToolError(
            "The requested tool is prohibited by the initialization checkpoint."
        )

    def _reserve(self, tool_name: str) -> Any:
        if self._session is None:
            raise SessionLimitViolationError("The one injected MCP session has not been attached.")
        if self._next_index >= MAX_DELEGATED_TOOL_CALLS:
            raise SessionLimitViolationError("The four-call session limit has been consumed.")
        expected = INITIALIZATION_TOOL_SEQUENCE[self._next_index]
        if tool_name != expected:
            raise CallSequenceError("A repeated, missing, or out-of-order checkpoint request was rejected.")
        self._next_index += 1
        return self._session

    @staticmethod
    async def _safe_call(call: Any) -> Any:
        try:
            return await call
        except LiveAdapterInitializationError:
            raise
        except Exception:
            raise ResponseShapeViolationError(
                "The broker tool call failed before a schema-valid response was available."
            ) from None

    async def get_accounts(self) -> AccountInitializationSummary:
        try:
            commissioned = CommissionedAccount.from_config(
                self._config_loader(),
                local_config=self._local_config_loader(),
            )
        except Exception:
            raise CommissionedIdentityMismatchError(
                "The commissioned account identity could not be established."
            ) from None

        session = self._reserve("get_accounts")
        result = await self._safe_call(session.call_tool("get_accounts", {}))
        data = _unwrap(result, "get_accounts")
        _exact_keys(data, required=frozenset({"accounts"}), label="get_accounts response data")
        rows = _array_or_null(data.get("accounts"), "get_accounts accounts")
        accounts = [_validate_account(row) for row in rows if row is not None]
        eligible = [
            account
            for account in accounts
            if account.get("agentic_allowed") is True
            and account.get("state") == "active"
            and account.get("deactivated") is not True
            and account.get("permanently_deactivated") is not True
        ]
        if len(eligible) != 1:
            raise CommissionedIdentityMismatchError(
                "Exactly one active agentic-accessible account is required."
            )
        try:
            selected = select_agentic_account([dict(eligible[0])])
            number = selected["account_number"]
            commissioned.verify(account_fingerprint(number))
        except Exception:
            raise CommissionedIdentityMismatchError(
                "The active broker identity does not match the commissioned identity."
            ) from None
        self._account_number = number
        return AccountInitializationSummary(
            record_count=len(accounts),
            eligible_account_count=1,
            identity_match=True,
        )

    async def get_equity_positions(self) -> PositionInitializationSummary:
        session = self._reserve("get_equity_positions")
        if self._account_number is None:
            raise CallSequenceError("Commissioned identity must be verified before account reads.")
        result = await self._safe_call(
            session.call_tool("get_equity_positions", {"account_number": self._account_number})
        )
        data = _unwrap(result, "get_equity_positions")
        _exact_keys(
            data,
            required=frozenset({"positions"}),
            optional=frozenset({"next"}),
            label="get_equity_positions response data",
        )
        _pagination_absent(data, "get_equity_positions")
        rows = _array_or_null(data.get("positions"), "get_equity_positions positions")
        nulls = 0
        count = 0
        for row in rows:
            if row is None:
                nulls += 1
                continue
            _validate_position(row)
            count += 1
        return PositionInitializationSummary(record_count=count, null_record_count=nulls)

    async def get_equity_orders(self) -> OrderInitializationSummary:
        session = self._reserve("get_equity_orders")
        if self._account_number is None:
            raise CallSequenceError("Commissioned identity must be verified before account reads.")
        result = await self._safe_call(
            session.call_tool("get_equity_orders", {"account_number": self._account_number})
        )
        data = _unwrap(result, "get_equity_orders")
        _exact_keys(
            data,
            required=frozenset({"orders"}),
            optional=frozenset({"next"}),
            label="get_equity_orders response data",
        )
        _pagination_absent(data, "get_equity_orders")
        rows = _array_or_null(data.get("orders"), "get_equity_orders orders")
        counts = {classification.value: 0 for classification in OrderStateClass}
        nulls = 0
        parsed_count = 0
        for row in rows:
            if row is None:
                nulls += 1
                continue
            try:
                parsed = parse_order_status(row)
            except ResponseContractViolationError:
                raise ResponseShapeViolationError(
                    "get_equity_orders contained a schema-invalid order record."
                ) from None
            counts[classify_order_state(parsed.state).value] += 1
            parsed_count += 1
        return OrderInitializationSummary(
            record_count=parsed_count,
            null_record_count=nulls,
            lifecycle_counts=tuple(sorted(counts.items())),
            active_protection_mapping_empty=(
                len(ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES) == 0
            ),
        )

    async def review_equity_order(self) -> ReviewInitializationSummary:
        session = self._reserve("review_equity_order")
        if self._account_number is None:
            raise CallSequenceError("Commissioned identity must be verified before review.")
        arguments = {"account_number": self._account_number, **FIXED_REVIEW_ARGUMENTS}
        result = await self._safe_call(session.call_tool("review_equity_order", arguments))
        data = _unwrap(result, "review_equity_order")
        _exact_keys(data, required=_REVIEW_REQUIRED, optional=_REVIEW_OPTIONAL, label="review response data")
        for field in ("symbol", "side", "type"):
            _text(data.get(field), f"review {field}")
        for field in ("quantity", "dollar_amount", "limit_price", "stop_price"):
            if field in data:
                _decimal_text(data.get(field), f"review {field}")
        if "market_data_disclosure" in data:
            _text(data.get("market_data_disclosure"), "review market_data_disclosure")
        _mapping(data.get("order_checks"), "review order_checks")
        quote = data.get("quote_data")
        if quote is not None:
            quote_map = _mapping(quote, "review quote_data")
            _exact_keys(quote_map, required=_QUOTE_REQUIRED, label="review quote_data")
            for field in _QUOTE_REQUIRED:
                if field == "has_traded":
                    _boolean(quote_map.get(field), f"review quote_data {field}")
                else:
                    _text(
                        quote_map.get(field),
                        f"review quote_data {field}",
                        nullable=field in _QUOTE_NULLABLE,
                    )
        rows: list[tuple[str, str]] = []
        booleans, objects, arrays = _shape(data, "data", rows)
        self._account_number = None
        return ReviewInitializationSummary(
            field_paths=tuple(path for path, _ in rows),
            json_types=tuple(rows),
            boolean_count=booleans,
            object_count=objects,
            array_count=arrays,
        )

    def assert_complete(self) -> InitializationSequenceSummary:
        if self._next_index != MAX_DELEGATED_TOOL_CALLS:
            raise CallSequenceError("The checkpoint sequence is incomplete.")
        return InitializationSequenceSummary(
            tool_names=INITIALIZATION_TOOL_SEQUENCE,
            delegated_call_count=self._next_index,
            session_count=int(self._session_attached),
            complete=True,
        )
